import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from shapely.geometry import Point, LineString, Polygon, box, mapping, shape
from shapely.ops import unary_union

from workflow.analysis import analyze_resources
from workflow.catalog import DEFAULTS, read_json
from workflow.compiler import prepare
from workflow.cartography import access_state, validate_parameters
from workflow.delivery import bundle, preview
from workflow.engine import execute
from workflow.ingest import normalize
from workflow.integrity import inspect_run
from workflow.providers.overture import RangeFile, extract
from workflow.repaint import repaint
from workflow.spatial import context
from workflow.task import new_task, add_instance, confirm, write_json


class CartographicContracts(unittest.TestCase):
    def setUp(self):
        self.task=new_task('有来源的制图算例')
        self.task['project'].update(source_crs='EPSG:32648',analysis_crs='EPSG:32648',boundary=mapping(box(500000,3400000,500100,3400100)))
        self.ctx=context(self.task['project'])

    def feature(self,fid,geom,**props): return {'id':fid,'geometry':geom,'properties':props}

    def data(self,records): return {'features':records,'sha256':'fixture','metadata':{'source':'fixture','data_period':'synthetic','acquired_at':'2026-10-26'}}

    def calculate(self,did,resources,**p):
        return analyze_resources(did,{**DEFAULTS[did],**p},resources,self.ctx,self.task['project'])

    def test_unknown_pedestrian_access_is_never_promoted_by_road_class(self):
        self.assertEqual(access_state({'class':'footway','access_restrictions':[]}),'通行未知')
        with self.assertRaisesRegex(ValueError,'旧版缓存'):access_state({'class':'footway'})

    def test_access_modes_temporal_partial_and_conflicts_are_conservative(self):
        self.assertEqual(access_state({'access_restrictions':[{'access_type':'allowed','when':{'mode':['foot']}}]}),'来源明确允许')
        self.assertEqual(access_state({'access_restrictions':[{'access_type':'denied','when':{'mode':['foot']}}]}),'来源明确禁止')
        self.assertEqual(access_state({'access_restrictions':[{'access_type':'denied','when':{'mode':['motor_vehicle']}}]}),'通行未知')
        for rule in [{'access_type':'allowed','when':{'during':'Mo-Fr'}},{'access_type':'allowed','between':[.2,.8]}]:
            self.assertEqual(access_state({'access_restrictions':[rule]}),'条件待核对')
        self.assertEqual(access_state({'access_restrictions':[{'access_type':'allowed'},{'access_type':'denied'}]}),'条件待核对')

    def test_arrow_struct_null_condition_fields_do_not_invent_restrictions(self):
        props={'access_restrictions':[{'access_type':'allowed','between':None,'when':{'mode':['foot'],'during':None,'heading':None,'using':None,'recognized':None,'vehicle':None}}]}
        self.assertEqual(access_state(props),'来源明确允许')

    def test_administrative_panels_require_real_containment_at_every_level(self):
        boundaries=self.data([self.feature('province',box(499900,3399900,500200,3400200),subtype='region',name='区域'),
                              self.feature('city',box(499990,3399990,500110,3400110),subtype='county',name='城市'),
                              self.feature('other',box(501000,3410000,501100,3410100),subtype='county',name='异地')])
        result=self.calculate('location',{'context_boundaries':boundaries},boundary_subtypes=['region','county'])
        self.assertEqual([p['label'] for p in result['panels']],['区域','城市'])
        with self.assertRaisesRegex(ValueError,'localadmin'):
            self.calculate('location',{'context_boundaries':boundaries})

    def test_circle_scope_does_not_generate_an_invented_project_axis(self):
        result=self.calculate('orient',{})
        self.assertIsNone(result['axis_bearing_deg'])
        self.assertEqual(result['data_hashes'],{})
        with self.assertRaisesRegex(ValueError,'axis_source'):
            self.calculate('orient',{},axis_bearing_deg=20)

    def junction_fixture(self,shift=0,cid='junction'):
        x=500050+shift;y=3400050
        roads=[self.feature(cid+'a',LineString([(x-10,y),(x+10,y)]),connectors=[{'connector_id':cid,'at':.5}],access_restrictions=[]),
               self.feature(cid+'b',LineString([(x,y),(x,y+10)]),connectors=[{'connector_id':cid,'at':0}],access_restrictions=[])]
        return roads,self.feature(cid,Point(x,y))

    def test_shared_connector_interior_counts_two_arms(self):
        roads,node=self.junction_fixture()
        result=self.calculate('connectionfield',{'roads':self.data(roads),'connectors':self.data([node])})
        self.assertEqual(result['statistic_total'],1)
        self.assertEqual(result['detail_table'][0]['degree'],3)

    def test_closed_road_counts_both_ends_without_duplicate_references(self):
        x,y=500050,3400050
        roads=[self.feature('loop',LineString([(x,y),(x+10,y),(x+10,y+10),(x,y)]),
                            connectors=[{'connector_id':'j','at':0},{'connector_id':'j','at':1},{'connector_id':'j','at':0}]),
               self.feature('spur',LineString([(x-10,y),(x,y)]),connectors=[{'connector_id':'j','at':1}])]
        result=self.calculate('connectionfield',{'roads':self.data(roads),'connectors':self.data([self.feature('j',Point(x,y))])})
        self.assertEqual(result['statistic_total'],1)
        self.assertEqual(result['detail_table'][0]['degree'],3)

    def test_crossing_roads_without_shared_connectors_do_not_create_a_junction(self):
        roads=[self.feature('a',LineString([(500000,3400050),(500100,3400050)]),connectors=[]),
               self.feature('b',LineString([(500050,3400000),(500050,3400100)]),connectors=[])]
        result=self.calculate('connectionfield',{'roads':self.data(roads),'connectors':self.data([])})
        self.assertEqual(result['statistic_total'],0)

    def test_grade_separated_connector_ids_are_not_merged_by_coincident_coordinates(self):
        a,na=self.junction_fixture(cid='ground');b,nb=self.junction_fixture(cid='bridge')
        result=self.calculate('connectionfield',{'roads':self.data(a+b),'connectors':self.data([na,nb])})
        self.assertEqual(result['statistic_total'],2)
        self.assertEqual(result['edge_count'],0)

    def test_missing_or_displaced_junction_point_is_rejected(self):
        roads,node=self.junction_fixture()
        for nodes in [[],[self.feature(node['id'],Point(500080,3400080))]]:
            with self.assertRaises(ValueError):self.calculate('connectionfield',{'roads':self.data(roads),'connectors':self.data(nodes)})

    def test_geometric_neighbors_are_distance_limited_and_links_are_deduplicated(self):
        roads=[];nodes=[]
        for shift,cid in [(-20,'a'),(0,'b'),(20,'c')]:
            r,n=self.junction_fixture(shift,cid);roads.extend(r);nodes.append(n)
        result=self.calculate('connectionfield',{'roads':self.data(roads),'connectors':self.data(nodes)},k_nearest=1,max_distance_m=21)
        self.assertEqual(result['statistic_total'],3);self.assertEqual(result['edge_count'],2)

    def test_generalisation_respects_road_water_and_preserved_courtyard(self):
        outer=box(500005,3400005,500095,3400095);hole=box(500015,3400015,500035,3400035)
        building=Polygon(outer.exterior.coords,[hole.exterior.coords]);road=LineString([(500050,3400000),(500050,3400100)])
        water=box(500070,3400050,500090,3400080)
        result=self.calculate('fabricplus',{'buildings':self.data([self.feature('b',building)]),'roads':self.data([self.feature('r',road)]),
                                          'green_water':self.data([self.feature('w',water,kind='water')])},preserve_holes_m2=100)
        output=unary_union([shape(f['geometry']) for layer in result['display_layers'] for f in layer['features']])
        self.assertLess(output.intersection(road.buffer(3)).area,1e-7)
        self.assertLess(output.intersection(water).area,1e-7)
        self.assertLess(output.intersection(hole).area,1e-7)
        self.assertAlmostEqual(result['original_area_m2'],building.area)
        self.assertIsNone(result['coverage_percent'])

    def test_overlay_order_and_per_resource_hashes_are_retained(self):
        resources={'buildings':self.data([self.feature('b',box(500010,3400010,500020,3400020))]),
                   'roads':self.data([self.feature('r',LineString([(500000,3400050),(500100,3400050)]))])}
        resources['roads']['sha256']='second-input'
        result=self.calculate('combined',resources,layers=['roads','buildings'])
        self.assertEqual([l['id'] for l in result['display_layers']],['roads','buildings'])
        self.assertEqual(result['data_hashes'],{'roads':'second-input','buildings':'fixture'})
        self.assertEqual(result['statistic_total'],2)

    def test_full_source_geometry_and_linear_reference_fields_survive_normalization(self):
        geom=LineString([(104.0,30.65),(104.2,30.65)])
        records,_=normalize('segment',[{'id':'r','subtype':'road','class':'primary','geometry':geom.wkb,
                                      'connectors':[{'connector_id':'c','at':.5}],'access_restrictions':[]}],self.ctx,[104.1,30.64,104.12,30.66])
        self.assertGreater(shape(records[0]['geometry']).length,15000)
        self.assertEqual(records[0]['properties']['connectors'][0]['at'],.5)
        self.assertEqual(records[0]['properties']['layer'],'unknown')

    def test_invalid_layer_and_boundary_lists_fail_preflight(self):
        for did,p in [('combined',{'layers':[{}]}),('combined',{'layers':42}),('context',{'boundary_subtype':[]}),('location',{'boundary_subtypes':[{},'county']})]:
            with self.assertRaises(ValueError):validate_parameters(did,{**DEFAULTS[did],**p})
            with tempfile.TemporaryDirectory() as temp:
                task=copy.deepcopy(self.task);item=add_instance(task,did);item['parameters']=p;confirm(task)
                path=Path(temp)/'task.json';write_json(path,task)
                _,plan,_,_=prepare(path)
                self.assertTrue(plan['instances'][0]['blockers'])

    def test_aligned_range_cache_preserves_bytes_seek_and_prevents_redundant_requests(self):
        raw=bytes(range(251))*9000
        class Response:
            def __init__(self,start,end):
                self.status_code=206;self.headers={'Content-Range':f'bytes {start}-{end}/{len(raw)}'};self.start=start;self.end=end
            def iter_content(self,size):yield raw[self.start:self.end+1]
            def close(self):pass
        class Session:
            calls=0
            def get(self,url,headers,**kwargs):
                self.calls+=1;start,end=map(int,headers['Range'][6:].split('-'));return Response(start,end)
            def close(self):pass
        with tempfile.TemporaryDirectory() as temp:
            session=Session()
            with RangeFile({'href':'https://example.invalid/test','file:size':len(raw)},temp,session=session) as handle:
                handle.seek(1234);self.assertEqual(handle.read(100),raw[1234:1334]);self.assertEqual(handle.tell(),1334)
                handle.seek(10000);self.assertEqual(handle.read(200),raw[10000:10200]);self.assertEqual(session.calls,1)
                handle.seek(1048500);self.assertEqual(handle.read(200),raw[1048500:1048700]);self.assertEqual(session.calls,2)

    def test_old_column_subset_cache_cannot_satisfy_transport_requirements(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'overture-segment.parquet';pq.write_table(pa.table({'id':['legacy']}),path)
            write_json(path.with_suffix('.source.json'),{'bbox':[0,0,10,10],'release':'2026-10-21.0','sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            with self.assertRaisesRegex(ValueError,'所需字段'):
                extract(temp,'segment',[1,1,2,2],offline=True,release='2026-10-21.0',required_columns=['connectors'])

    def test_rich_cache_is_found_when_a_legacy_subset_coexists(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for path,rich in [(root/'overture-segment.parquet',False),(root/'2026-10-21.0/segment/rich.parquet',True)]:
                path.parent.mkdir(parents=True,exist_ok=True)
                columns={'id':['rich' if rich else 'legacy'],'bbox':pa.array([{'xmin':2.,'ymin':2.,'xmax':3.,'ymax':3.}])}
                if rich:columns['connectors']=pa.array([[]],type=pa.list_(pa.string()))
                pq.write_table(pa.table(columns),path)
                write_json(path.with_suffix('.source.json'),{'bbox':[0,0,10,10],'release':'2026-10-21.0'})
            table,_=extract(root,'segment',[1,1,4,4],offline=True,release='2026-10-21.0',required_columns=['connectors'])
            self.assertEqual(table['id'].to_pylist(),['rich'])

    def test_seven_modes_export_freeze_repaint_bundle_and_audit_all_input_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);task=copy.deepcopy(self.task);task['is_example']=True;task['output_formats']=['svg']
            roads,node=self.junction_fixture()
            fixtures={'roads':roads,'connectors':[node],'buildings':[self.feature('b',box(500010,3400010,500080,3400080))],
                      'green_water':[self.feature('water',box(500070,3400070,500090,3400090),kind='water')],
                      'context_boundaries':[self.feature(s,box(499900-i*50,3399900-i*50,500200+i*50,3400200+i*50),subtype=s,name=s)
                                            for i,s in enumerate(['region','county','localadmin'])]}
            for rid,records in fixtures.items():
                path=root/f'{rid}.geojson';write_json(path,{'type':'FeatureCollection','features':[{'type':'Feature',**f,'geometry':mapping(f['geometry'])} for f in records]})
                task['datasets'][rid]={'path':path.name,'source':'synthetic','acquired_at':'2026-10-26','data_period':'synthetic','crs':'EPSG:32648',
                                       'units':'metres','coverage':task['project']['boundary'],'completeness':'complete','mapping_completeness':'verified_complete','estimated_fields':[]}
            for did in ['context','location','orient','pedestrian','combined','fabricplus','connectionfield']:add_instance(task,did)
            confirm(task);write_json(root/'task.json',task)
            run,m=execute(root/'task.json',root/'runs')
            self.assertEqual(m['status'],'needs_review',[(i['instance_id'],i.get('error')) for i in m['instances']])
            self.assertEqual(m['generated_file_count'],7);self.assertTrue(inspect_run(run)['passed']);self.assertTrue(preview(run).exists())
            for rid in fixtures:(root/f'{rid}.geojson').unlink()
            new,updated=repaint(run,{'palette':'monochrome'})
            self.assertTrue(inspect_run(new)['passed'])
            packet=root/'packet.zip';bundle(new,packet)
            with zipfile.ZipFile(packet) as z:self.assertIn('workflow/cartography.py',z.namelist())
            item=next(i for i in updated['instances'] if i['diagram_id']=='combined')
            record=new/item['analysis_file']['path'];analysis=read_json(record);analysis['data_hashes']['green_water']='wrong-input-version';write_json(record,analysis)
            item['analysis_file']['sha256']=hashlib.sha256(record.read_bytes()).hexdigest();write_json(new/'manifest.json',updated)
            self.assertFalse(inspect_run(new)['passed'])


if __name__=='__main__':unittest.main()
