import copy
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

from shapely.geometry import Point,box,mapping,shape
from shapely.ops import unary_union

from workflow.analysis import analyze_resources
from workflow.catalog import DEFAULTS,POINT_DIAGRAMS,read_json
from workflow.compiler import prepare
from workflow.delivery import preview,bundle
from workflow.engine import execute
from workflow.ingest import normalize,source_attributions
from workflow.integrity import inspect_run
from workflow.points import select_points,validate_parameters,STATION_CATEGORIES
from workflow.repaint import repaint
from workflow.spatial import context,file_hash
from workflow.task import new_task,add_instance,confirm,write_json


class PointEvidence(unittest.TestCase):
    def setUp(self):
        self.x,self.y=500000,3400000
        self.task=new_task('点资料算例')
        boundary=box(self.x,self.y,self.x+100,self.y+100)
        self.task['project'].update(source_crs='EPSG:32648',analysis_crs='EPSG:32648',boundary=mapping(boundary),
                                    scopes={'analysis':mapping(boundary),'display':mapping(boundary),'acquisition':mapping(boundary.buffer(1000))})
        self.ctx=context(self.task['project'])

    def feature(self,fid,x,y,primary='hospital',name=None,confidence=1,top='health_care',**props):
        return {'id':fid,'geometry':Point(self.x+x,self.y+y),'properties':{'name':name if name is not None else fid,
                'taxonomy':{'primary':primary,'hierarchy':[top,primary]},'confidence':confidence,**props}}

    def data(self,features):
        return {'features':features,'sha256':'fixture','metadata':{'source':'fixture','data_period':'synthetic','acquired_at':'2026-10-27'}}

    def calculate(self,did,features,stations=None,**params):
        resources={'pois':self.data(features)}
        if stations is not None:resources['transit_stops']=self.data(stations)
        return analyze_resources(did,{**DEFAULTS[did],**params},resources,self.ctx,self.task['project'])

    def test_taxonomy_categories_are_source_backed_and_missing_is_unknown(self):
        unknown=self.feature('unknown',70,70);unknown['properties'].pop('taxonomy')
        r=self.calculate('poitype',[self.feature('school',10,10,'school',top='education'),unknown])
        self.assertEqual({x['category'] for x in r['table']},{'教育','分类未知'})
        self.assertEqual(r['statistic_total'],2)
        selected=self.calculate('poitype',[self.feature('school',10,10,'school',top='education')],categories=['education'])
        self.assertEqual(selected['statistic_total'],1)

    def test_suspicious_hospital_station_name_is_flagged_without_reclassification(self):
        r=self.calculate('poitype',[self.feature('suspicious',20,20,name='市二医院地铁站 Metro Station')])
        self.assertEqual(r['detail_table'][0]['source_category'],'hospital')
        self.assertEqual(r['statistic_total'],1)
        self.assertEqual(r['quality_flags'][0]['source_id'],'suspicious')
        self.assertIn('未据名称更改',r['detail_table'][0]['quality_warning'])

    def test_dedup_does_not_merge_colocated_different_businesses_or_unnamed_points(self):
        points=[self.feature('a',20,20,name='Same'),self.feature('b',20,20,name=' same '),
                self.feature('other',20,20,name='Other'),self.feature('n1',20,20,name=''),self.feature('n2',20,20,name='')]
        r=self.calculate('poitype',points)
        self.assertEqual(r['statistic_total'],4)
        self.assertEqual(r['detail_table'][0]['source_ids'],'a;b')
        self.assertEqual(r['selection_audit'][0]['dropped']['exact_name_category_coordinate_duplicate'],1)

    def test_confidence_status_and_unknown_policy_are_recorded(self):
        records=[self.feature('ok',10,10),self.feature('unknown',20,20,confidence=None),self.feature('low',30,30,confidence=.1),
                 self.feature('closed',40,40,operating_status='permanently_closed'),self.feature('hiatus',50,50,operating_status='temporarily_closed')]
        r=self.calculate('poitype',records)
        self.assertEqual(r['statistic_total'],3)
        self.assertEqual(r['selection_audit'][0]['unknown_confidence_kept'],1)
        self.assertEqual(self.calculate('poitype',records,unknown_confidence='exclude')['statistic_total'],2)
        with self.assertRaisesRegex(ValueError,'confidence'):self.calculate('poitype',[self.feature('bad',20,20,confidence=float('nan'))])

    def test_invalid_mode_parameters_and_insufficient_halo_fail_preflight(self):
        for did,params in [('poitype',{'categories':[{}]}),('sitemap',{'max_labels':True}),('poiheat',{'bandwidth_m':-1}),('voronoi',{'mode':'network'})]:
            with self.assertRaises(ValueError):validate_parameters(did,{**DEFAULTS[did],**params},self.ctx)
        task=copy.deepcopy(self.task);task['project']['scopes']['acquisition']=task['project']['boundary'];ctx=context(task['project'])
        with self.assertRaisesRegex(ValueError,'取数缓冲'):validate_parameters('poiheat',DEFAULTS['poiheat'],ctx)
        with tempfile.TemporaryDirectory() as tmp:
            item=add_instance(task,'poitype');item['parameters']={'categories':[{}]};confirm(task);path=Path(tmp)/'task.json';write_json(path,task)
            self.assertTrue(prepare(path)[1]['instances'][0]['blockers'])

    def test_grid_conserves_points_on_interior_lines_and_closed_outer_boundary(self):
        records=[self.feature('origin',0,0),self.feature('mid',50,50),self.feature('end',100,100),self.feature('side',100,50)]
        r=self.calculate('urbangrid',records,cell_size_m=50)
        self.assertEqual(r['statistic_total'],4)
        self.assertEqual(sum(row['point_count'] for row in r['detail_table']),4)
        self.assertEqual(sum(row['effective_area_m2'] for row in r['detail_table']),10000)
        self.assertEqual(len(r['point_assignments']),4)
        self.assertEqual(sum(row['value'] for row in r['table']),4)

    def test_grid_keeps_zero_cells_and_uses_clipped_effective_area(self):
        task=copy.deepcopy(self.task);task['project']['scopes']['analysis']=mapping(box(self.x,self.y,self.x+75,self.y+100));ctx=context(task['project'])
        r=analyze_resources('urbangrid',{**DEFAULTS['urbangrid'],'cell_size_m':50},{'pois':self.data([self.feature('edge',60,10)])},ctx,task['project'])
        occupied=next(row for row in r['detail_table'] if row['point_count'])
        self.assertEqual(occupied['effective_area_m2'],1250)
        self.assertEqual(occupied['points_per_km2'],800)
        self.assertEqual(sum(row['point_count']==0 for row in r['detail_table']),3)

    def test_density_matches_analytic_kernel_and_never_claims_grid_count(self):
        r=self.calculate('poiheat',[self.feature('center',50,50)],cell_size_m=100,bandwidth_m=10,truncate_sigma=2)
        expected=1e6/(2*math.pi*100*(1-math.exp(-2)))
        self.assertAlmostEqual(r['peak_density_per_km2'],expected)
        self.assertEqual(r['statistic_unit'],'格')
        self.assertEqual(r['source_point_count_in_analysis'],1)

    def test_density_uses_halo_points_outside_analysis(self):
        r=self.calculate('poiheat',[self.feature('halo',110,87.5)],cell_size_m=25,bandwidth_m=10,truncate_sigma=3)
        self.assertEqual(r['source_point_count_in_analysis'],0)
        self.assertEqual(r['halo_point_count'],1)
        self.assertGreater(r['peak_density_per_km2'],0)

    def test_voronoi_external_competitor_changes_cell_and_areas_partition_scope(self):
        r=self.calculate('voronoi',[self.feature('in',50,50),self.feature('out',110,50)])
        external=next(row for row in r['detail_table'] if row['site_id']=='out')
        self.assertFalse(external['seed_inside_analysis'])
        self.assertAlmostEqual(external['cell_area_m2'],2000)
        self.assertEqual(r['edge_uncertain_area_m2'],0)
        polys=[shape(f['geometry']) for f in r['display_cells']]
        self.assertAlmostEqual(sum(g.area for g in polys),10000)
        self.assertAlmostEqual(unary_union(polys).area,10000)

    def test_voronoi_colocated_entities_share_one_cell_without_jitter(self):
        r=self.calculate('voronoi',[self.feature('a',25,50),self.feature('b',25,50,name='different'),self.feature('c',75,50)])
        self.assertEqual(r['site_group_count'],2)
        shared=next(row for row in r['detail_table'] if row['coincident_records']==2)
        self.assertEqual(shared['source_ids'],'a;b')
        self.assertEqual(shared['seed_x_m'],self.x+25)

    def test_voronoi_finite_acquisition_marks_uncertainty_and_caps_work(self):
        task=copy.deepcopy(self.task);task['project']['scopes']['acquisition']=task['project']['boundary'];ctx=context(task['project'])
        resources={'pois':self.data([self.feature('single',50,50)])}
        r=analyze_resources('voronoi',DEFAULTS['voronoi'],resources,ctx,task['project'])
        self.assertEqual(r['edge_uncertain_area_m2'],10000)
        with self.assertRaisesRegex(ValueError,'max_sites'):self.calculate('voronoi',[self.feature('a',25,50),self.feature('b',75,50)],max_sites=1)

    def test_station_count_excludes_self_and_uses_only_explicit_station_categories(self):
        station=self.feature('station',50,50,'metro_station',top='travel_and_transportation')
        other=self.feature('hospital',60,50)
        r=self.calculate('urbanstations',[station,other],stations=[station],categories=['health_care'],radius_m=30)
        self.assertEqual(r['detail_table'][0]['nearby_source_records'],1)
        self.assertEqual(r['statistic_total'],1)
        with self.assertRaisesRegex(ValueError,'没有'):self.calculate('urbanstations',[other],stations=[])
        gas=self.feature('gas',50,50,'gas_station',name='Metro Station')
        with self.assertRaisesRegex(ValueError,'无明确站点'):self.calculate('urbanstations',[gas],stations=[gas])

    def test_node_concentration_selects_real_points_with_spacing_and_stable_ties(self):
        points=[self.feature('a',20,20),self.feature('b',25,20),self.feature('c',80,80)]
        r=self.calculate('nodes',points,radius_m=10,min_separation_m=40,max_nodes=5)
        self.assertEqual(r['analysis_feature_ids'],['a','c'])
        self.assertEqual([x['neighbor_count'] for x in r['detail_table']],[1,0])
        self.assertEqual(r['display_points'][0]['geometry'],mapping(points[0]['geometry']))

    def test_nearest_index_uses_project_point_distance_and_retains_complete_names(self):
        r=self.calculate('sitemap',[self.feature('far',0,0),self.feature('near',55,50,name='完整的设施名称')],max_labels=1)
        self.assertEqual(r['analysis_feature_ids'],['near'])
        self.assertAlmostEqual(r['detail_table'][0]['straight_distance_m'],5)
        self.assertEqual(r['detail_table'][0]['name'],'完整的设施名称')

    def test_verified_empty_sources_are_explicit_except_missing_stations(self):
        for did in POINT_DIAGRAMS-{'urbanstations'}:
            r=self.calculate(did,[])
            self.assertEqual(r['source_point_count_in_analysis'],0)
            self.assertEqual(sum(row['value'] for row in r['table']),r['statistic_total'])

    def test_place_normalization_preserves_new_schema_and_uses_point_geometry(self):
        row={'id':'p','geometry':Point(104.1126,30.6507).wkb,'names':{'primary':'English','common':[('zh','中文名')]},
             'taxonomy':{'primary':'hospital','hierarchy':['health_care','hospital']},'confidence':.9}
        features,_=normalize('place',[row],self.ctx,[104,30,105,31])
        self.assertEqual(features[0]['properties']['name'],'中文名')
        self.assertEqual(features[0]['properties']['map_class'],'医疗健康')
        self.assertEqual(features[0]['geometry']['type'],'Point')

    def test_place_attribution_does_not_apply_building_odbl_to_places(self):
        records=[{'properties':{'sources':[{'dataset':'Foursquare','property':''},{'dataset':'meta','property':''}]}}]
        _,credits=source_attributions(records,places=True)
        self.assertTrue(any('Apache-2.0' in line for line in credits))
        self.assertFalse(any('OpenStreetMap' in line for line in credits))

    def test_seven_modes_freeze_export_repaint_and_bundle_all_resources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);task=copy.deepcopy(self.task);task['is_example']=True;task['output_formats']=['svg']
            station=self.feature('station',30,30,'metro_station',top='travel_and_transportation')
            resources={'pois':[station,self.feature('h',60,60)],'transit_stops':[station]}
            for rid,records in resources.items():
                path=root/f'{rid}.geojson';write_json(path,{'type':'FeatureCollection','features':[{'type':'Feature',**f,'geometry':mapping(f['geometry'])} for f in records]})
                task['datasets'][rid]={'path':path.name,'source':'synthetic','data_period':'synthetic','acquired_at':'2026-10-27','crs':'EPSG:32648',
                    'units':'metres','coverage':task['project']['scopes']['acquisition'],'completeness':'complete','estimated_fields':[],'mapping_completeness':'verified_complete'}
            for did in sorted(POINT_DIAGRAMS):add_instance(task,did)
            confirm(task);path=root/'task.json';write_json(path,task)
            run,m=execute(path,root/'output')
            self.assertEqual(m['status'],'needs_review',[(i['instance_id'],i.get('error')) for i in m['instances']])
            self.assertEqual(m['generated_file_count'],7)
            preview(run);self.assertTrue(inspect_run(run)['passed'])
            for rid in resources:(root/f'{rid}.geojson').unlink()
            repainted,_=repaint(run,{'palette':'monochrome'})
            self.assertTrue(inspect_run(repainted)['passed'])
            package=root/'points.zip';bundle(repainted,package)
            with zipfile.ZipFile(package) as z:
                self.assertIn('workflow/points.py',z.namelist());self.assertIn('workflow/points_render.py',z.namelist())
                self.assertIn('catalog/licenses/Foursquare-NOTICE.txt',z.namelist())
            item=next(i for i in m['instances'] if i['diagram_id']=='urbanstations');analysis=run/item['analysis_file']['path']
            data=read_json(analysis);data['data_hashes']['transit_stops']='0'*64;write_json(analysis,data)
            item['analysis_file']['sha256']=file_hash(analysis);write_json(run/'manifest.json',m)
            self.assertFalse(inspect_run(run)['passed'])
