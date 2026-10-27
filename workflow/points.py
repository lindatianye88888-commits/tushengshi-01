"""Point evidence, finite-support density and geometric nearest-site analysis."""
from bisect import bisect_right
from collections import Counter, defaultdict
import math
import re

import numpy as np
from shapely import STRtree, get_coordinates, voronoi_polygons
from shapely.geometry import Point, MultiPoint, LineString, box, mapping
from shapely.ops import unary_union

from .catalog import DEFAULTS
from .cartography import center_point, source_summary, table_row
from .spatial import digest
from .statistics import finite_number

POINTS_VERSION = "point-evidence-0.5.1"
STATION_CATEGORIES = {"train_station","railway_station","metro_station","subway_station","bus_station","bus_stop","tram_stop","ferry_terminal"}
CATEGORY_LABELS = {"food_and_drink":"餐饮","shopping":"购物","lodging":"住宿","travel_and_transportation":"交通出行",
    "services_and_business":"商务服务","arts_and_entertainment":"文娱","education":"教育","cultural_and_historic":"文化历史",
    "sports_and_recreation":"运动休闲","health_care":"医疗健康","community_and_government":"社区公共服务",
    "lifestyle_services":"生活服务","geographic_entities":"地理实体"}
STATION_LABELS = {"train_station":"铁路站点","railway_station":"铁路站点","metro_station":"地铁站点","subway_station":"地铁站点",
                  "bus_station":"公交/客运站点","bus_stop":"公交站点","tram_stop":"有轨站点","ferry_terminal":"轮渡站点"}


def validate_parameters(did,p,ctx=None):
    if p.get('mode')!=DEFAULTS[did]['mode']: raise ValueError(f"{did} 当前仅支持 mode={DEFAULTS[did]['mode']}。")
    cats=p.get('categories')
    if not isinstance(cats,list) or any(not isinstance(x,str) or not x for x in cats) or len(set(cats))!=len(cats):
        raise ValueError('categories 需为不重复的来源taxonomy类别字符串列表；空列表表示全部。')
    if not finite_number(p.get('min_confidence')) or not 0<=p['min_confidence']<=1: raise ValueError('min_confidence 需要[0,1]。')
    if not isinstance(p.get('unknown_confidence'),str) or p['unknown_confidence'] not in {'include','exclude'}: raise ValueError('unknown_confidence 需要include或exclude。')
    for key in ('deduplicate','exclude_permanently_closed'):
        if not isinstance(p.get(key),bool):raise ValueError(f'{key} 需要true/false。')
    for key,low,high in [('max_labels',1,24),('max_sites',1,20000),('max_nodes',1,40)]:
        if key in p and (not isinstance(p[key],int) or isinstance(p[key],bool) or not low<=p[key]<=high):raise ValueError(f'{key} 需要{low}至{high}的整数。')
    for key,low,high in [('cell_size_m',25,5000),('bandwidth_m',10,2000),('truncate_sigma',2,5),('radius_m',10,2000),('min_separation_m',0,5000)]:
        if key in p and (not finite_number(p[key]) or not low<=p[key]<=high):raise ValueError(f'{key} 需要[{low},{high}]米或约定倍数。')
    if ctx:
        if did in {'poiheat','urbangrid'}:
            x0,y0,x1,y1=ctx.scopes['analysis'].bounds;size=p['cell_size_m']
            if (math.floor(x1/size)-math.floor(x0/size)+1)*(math.floor(y1/size)-math.floor(y0/size)+1)>20000:
                raise ValueError('点网格超过20000格，请增大cell_size_m或缩小范围。')
        halo=p['bandwidth_m']*p['truncate_sigma'] if did=='poiheat' else p['radius_m'] if did in {'nodes','urbanstations'} else 0
        if halo and not ctx.scopes['acquisition'].buffer(.001).covers(ctx.scopes['analysis'].buffer(halo)):
            raise ValueError(f'此模式需要至少{halo:g}米取数缓冲；扩大acquisition，不能用分析区内的点补造边缘密度。')


def taxonomy(props):
    tax=props.get('taxonomy') or {}
    if not isinstance(tax,dict):raise ValueError('taxonomy 必须为来源对象。')
    primary=tax.get('primary') or props.get('basic_category') or ''
    hierarchy=tax.get('hierarchy') or []
    if not isinstance(primary,str) or not isinstance(hierarchy,list) or any(not isinstance(x,str) for x in hierarchy):raise ValueError('taxonomy.primary/hierarchy类型无效。')
    top=hierarchy[0] if hierarchy else ''
    return primary,hierarchy,CATEGORY_LABELS.get(top,'其他来源类型 · '+top if top else '分类未知')


def select_points(rid,resource,p,ctx,*,filter_categories=True):
    selected=[];dropped=Counter();by_key={};unknown_confidence=0
    excluded={str(v) for v in p.get('exclude_ids',[])}
    for f in sorted(resource['features'],key=lambda f:f['id']):
        props=f['properties'];geom=f['geometry']
        if f['id'] in excluded or f"{rid}/{f['id']}" in excluded:dropped['explicit_exclusion']+=1;continue
        if not ctx.scopes['acquisition'].covers(geom):dropped['outside_acquisition']+=1;continue
        confidence=props.get('confidence')
        if confidence is not None and (not finite_number(confidence) or not 0<=confidence<=1):raise ValueError('confidence需要[0,1]或null，不能修成零。')
        if props.get('operating_status')=='permanently_closed' and p['exclude_permanently_closed']:dropped['permanently_closed']+=1;continue
        if confidence is None and p['unknown_confidence']=='exclude':dropped['unknown_confidence']+=1;continue
        if confidence is not None and confidence<p['min_confidence']:dropped['below_confidence']+=1;continue
        primary,hierarchy,category=taxonomy(props)
        if filter_categories and p['categories'] and not set(p['categories']).intersection([primary,props.get('basic_category'),*hierarchy]):dropped['category_filter']+=1;continue
        if rid=='transit_stops' and primary not in STATION_CATEGORIES:raise ValueError('transit_stops中存在无明确站点类别的对象，不从名称猜站点。')
        name=props.get('name') or ''
        if not isinstance(name,str):raise ValueError('设施name需要字符串。')
        key=(geom.x,geom.y,' '.join(name.casefold().split()),primary)
        if p['deduplicate'] and name.strip() and key in by_key:
            by_key[key]['source_ids'].append(f['id']);dropped['exact_name_category_coordinate_duplicate']+=1;continue
        record={'id':f['id'],'source_ids':[f['id']],'geometry':geom,'name':name,'primary':primary,'category':category,
                'confidence':confidence,'operating_status':props.get('operating_status'),'quality_warning':''}
        if 'hospital' in [primary,*hierarchy] and re.search(r'地铁站|公交站|火车站|(?:metro|subway|railway|train)\s+station',name,re.I):
            record['quality_warning']='来源医院类名称含站点词，需人工核对；未据名称更改分类。'
        selected.append(record);by_key[key]=record
        if confidence is None:unknown_confidence+=1
    return selected,{'resource':rid,'input_count':len(resource['features']),'kept_count':len(selected),'unknown_confidence_kept':unknown_confidence,'dropped':dict(dropped)}


def point_record(f,category=None,**extra):
    return {'id':f['id'],'source_ids':f['source_ids'],'name':f['name'],'category':category or f['category'],'geometry':mapping(f['geometry']),**extra}


def detail(f,**extra):
    return {'source_id':f['id'],'source_ids':';'.join(f['source_ids']),'name':f['name'],'source_category':f['primary'],
            'category':f['category'],'x_m':f['geometry'].x,'y_m':f['geometry'].y,'confidence':f['confidence'],
            'operating_status':f['operating_status'],'quality_warning':f.get('quality_warning',''),**extra}


def count_table(records,key='category'):
    counts=Counter(f[key] for f in records)
    return [table_row(k,n,'个') for k,n in sorted(counts.items(),key=lambda kv:(-kv[1],kv[0]))]


def source_categories(points,ctx,center,p):
    inside=[f for f in points if ctx.scopes['analysis'].covers(f['geometry'])]
    return {'analysis_feature_ids':[f['id'] for f in inside],'display_points':[point_record(f) for f in inside],
            'detail_table':[detail(f) for f in inside],'table':count_table(inside),'statistic_total':len(inside),'statistic_unit':'个',
            'headline':f'{len(inside)} 个','headline_caption':'筛选与保守去重后的来源设施',
            'method':'按来源taxonomy.hierarchy首级分类；未知保留未知。只合并同坐标、同非空名称及同主类的记录，保留全部来源ID；不按距离合并邻近商户。'}


def nearest_index(points,ctx,center,p):
    inside=[f for f in points if ctx.scopes['analysis'].covers(f['geometry'])]
    chosen=sorted(inside,key=lambda f:(f['geometry'].distance(center),f['id']))[:p['max_labels']]
    labels=[point_record(f,number=i+1,distance_m=f['geometry'].distance(center)) for i,f in enumerate(chosen)]
    return {'analysis_feature_ids':[f['id'] for f in chosen],'display_points':labels,'context_points':[point_record(f) for f in inside],
            'detail_table':[detail(f,number=i+1,straight_distance_m=f['geometry'].distance(center)) for i,f in enumerate(chosen)],
            'table':count_table(chosen),'statistic_total':len(chosen),'statistic_unit':'个','candidate_count':len(inside),
            'headline':f'{len(chosen)} 处','headline_caption':'距项目点最近的来源设施 · 直线距离',
            'method':f"在分析区按项目点米制直线距离选取最近{p['max_labels']}条记录；图中名称与编号对应完整明细。此距离不是入口、道路或步行距离。"}


def grid_cells(boundary,size):
    x0,y0,x1,y1=boundary.bounds;cells={}
    for i in range(math.floor(x0/size),math.floor(x1/size)+1):
        for j in range(math.floor(y0/size),math.floor(y1/size)+1):
            g=box(i*size,j*size,(i+1)*size,(j+1)*size).intersection(boundary)
            if g.area>0:cells[(i,j)]={'id':f'{i}:{j}','geometry':g,'count':0}
    return cells


def point_grid(points,ctx,center,p):
    cells=grid_cells(ctx.scopes['analysis'],p['cell_size_m']);inside=[f for f in points if ctx.scopes['analysis'].covers(f['geometry'])]
    assignments={}
    for f in inside:
        pt=f['geometry'];i,j=math.floor(pt.x/p['cell_size_m']),math.floor(pt.y/p['cell_size_m']);key=(i,j)
        if key not in cells:
            matches=sorted(k for k in ((a,b) for a in range(i-1,i+2) for b in range(j-1,j+2)) if k in cells and cells[k]['geometry'].covers(pt))
            if not matches:raise ValueError('边界点无法归属有效格，请核对范围精度。')
            key=matches[0]
        cells[key]['count']+=1;assignments[f['id']]=cells[key]['id']
    rows=[];shown=[];totals=Counter();classes=['0个','1–5个','6–10个','11–20个','21–50个','>50个']
    for cell in cells.values():
        n=cell['count'];category=classes[bisect_right([0,5,10,20,50],n-1)]
        totals[category]+=n
        rows.append({'cell_id':cell['id'],'point_count':n,'effective_area_m2':cell['geometry'].area,'points_per_km2':n/cell['geometry'].area*1e6})
        shown.append({'id':cell['id'],'geometry':mapping(cell['geometry']),'category':category,'value':n})
    assert sum(c['count'] for c in cells.values())==len(inside)
    return {'analysis_feature_ids':[f['id'] for f in inside],'display_cells':shown,'display_points':[],
            'detail_table':rows,'point_assignments':assignments,'table':[table_row(k,totals[k],'个') for k in classes],
            'statistic_total':len(inside),'statistic_unit':'个','grid_count':len(cells),'cell_size_m':p['cell_size_m'],
            'headline':f'{len(inside)} 个','headline_caption':f"{p['cell_size_m']:g}米固定网格 · 来源点数",
            'method':'投影坐标原点固定网格；内部格线点按右/上格归属，外边界按有效邻格稳定归属；每点仅计一次。明细密度以裁剪后的有效格面积为分母，零点格保留。'}


def gaussian_density(points,ctx,center,p):
    cells=grid_cells(ctx.scopes['analysis'],p['cell_size_m']);geoms=[f['geometry'] for f in points];tree=STRtree(geoms)
    h=p['bandwidth_m'];radius=h*p['truncate_sigma'];normalizer=2*math.pi*h*h*(1-math.exp(-p['truncate_sigma']**2/2))
    classes=['0','0–100','100–300','300–1000','1000–3000','≥3000'];totals=Counter();shown=[];rows=[]
    for cell in cells.values():
        g=cell['geometry'];sample=g.centroid
        if not g.covers(sample):sample=g.representative_point()
        distances=[sample.distance(geoms[int(j)]) for j in tree.query(sample.buffer(radius).envelope)]
        value=sum(math.exp(-d*d/(2*h*h)) for d in distances if d<=radius)/normalizer*1e6
        category=classes[0] if value==0 else classes[1+bisect_right([100,300,1000,3000],value)]
        totals[category]+=1
        rows.append({'cell_id':cell['id'],'sample_x_m':sample.x,'sample_y_m':sample.y,'effective_area_m2':g.area,'source_records_per_km2':value})
        shown.append({'id':cell['id'],'geometry':mapping(g),'category':category,'value':value})
    inside=[f for f in points if ctx.scopes['analysis'].covers(f['geometry'])]
    peak=max((r['source_records_per_km2'] for r in rows),default=0)
    return {'analysis_feature_ids':[f['id'] for f in inside],'display_cells':shown,'display_points':[point_record(f) for f in inside],
            'detail_table':rows,'table':[table_row(k,totals[k],'格') for k in classes],'statistic_total':len(cells),'statistic_unit':'格',
            'grid_count':len(cells),'cell_size_m':p['cell_size_m'],'bandwidth_m':h,'support_radius_m':radius,'peak_density_per_km2':peak,
            'halo_point_count':len(points)-len(inside),'headline':f'{peak:,.0f} 个/km²','headline_caption':'来源记录核密度 · 格内采样峰值',
            'method':f"二维高斯核带宽{h:g}米，截断于{p['truncate_sigma']:g}σ（{radius:g}米）并归一化；使用分析区外取数缓冲点。{p['cell_size_m']:g}米格内采样，单位来源记录/km²；不是人流或格内实际点数，不保证离散积分等于记录数。"}


def euclidean_partition(points,ctx,center,p):
    groups={}
    for f in points:groups.setdefault((f['geometry'].x,f['geometry'].y),[]).append(f)
    if len(groups)>p['max_sites']:raise ValueError('最近点分区超出max_sites；缩小范围或明确筛选设施类别，不静默抽样。')
    coords=list(groups);sites=[Point(c) for c in coords];boundary=ctx.scopes['analysis']
    if not sites:polys=[]
    elif len(sites)==1:polys=[ctx.scopes['acquisition'].envelope]
    else:
        # Associate each GEOS cell with its contained source point rather than
        # assuming polygon order or requiring GEOS ordered-output support.
        raw=list(voronoi_polygons(MultiPoint(sites),extend_to=ctx.scopes['acquisition'].envelope).geoms)
        tree=STRtree(raw);polys=[]
        for site in sites:
            candidates=[raw[int(j)] for j in tree.query(site) if raw[int(j)].covers(site)]
            if len(candidates)!=1:raise ValueError('最近点分区不能唯一对应来源点。')
            polys.append(candidates[0])
    shown=[];rows=[];areas=Counter();uncertain=0
    for coord,site,poly in zip(coords,sites,polys):
        g=poly.intersection(boundary)
        if g.area<=0:continue
        members=groups[coord];category=members[0]['category'] if len({f['category'] for f in members})==1 else '共址多类设施'
        distances=np.linalg.norm(get_coordinates(g)-np.array(coord),axis=1)
        # Sufficient conservative bound: a whole cell is certified only when
        # its farthest seed distance fits within its nearest acquisition edge.
        edge_uncertain=float(np.max(distances))>g.distance(ctx.scopes['acquisition'].boundary)+1e-6
        if edge_uncertain:uncertain+=g.area
        areas[category]+=g.area
        ids=[sid for f in members for sid in f['source_ids']]
        number=len(shown)+1
        shown.append({'id':members[0]['id'],'number':number,'geometry':mapping(g),'category':category,'edge_uncertain':edge_uncertain})
        rows.append({'number':number,'site_id':members[0]['id'],'source_ids':';'.join(ids),'names':';'.join(f['name'] for f in members),
                     'category':category,'coincident_records':len(members),'seed_x_m':site.x,'seed_y_m':site.y,'seed_inside_analysis':boundary.covers(site),'cell_area_m2':g.area,'edge_uncertain':edge_uncertain})
    covered=sum(areas.values())
    if sites and (not math.isclose(covered,boundary.area,rel_tol=1e-8) or not math.isclose(unary_union([poly.intersection(boundary) for poly in polys]).area,covered,rel_tol=1e-8)):
        raise ValueError('最近点分区面积守恒或重叠检查失败。')
    inside=[f for f in points if boundary.covers(f['geometry'])]
    return {'analysis_feature_ids':[f['id'] for f in inside],'display_cells':shown,'display_points':[point_record(f) for f in inside],
            'detail_table':rows,'table':[table_row(k,a,'m²') for k,a in sorted(areas.items())],'statistic_total':covered,'statistic_unit':'m²',
            'site_group_count':len(groups),'partition_count':len(shown),'edge_uncertain_area_m2':uncertain,'partition_area_m2':covered,
            'headline':f'{len(shown)} 区','headline_caption':('来源类别 '+', '.join(p['categories'])+' · 最近分区') if p['categories'] else '已获取设施点的欧氏最近分区',
            'method':'分析区外已获取点参与竞争，共址设施共用一格，不抖动点位。逐格裁剪并核对面积守恒；斜纹区域未能保证取数区外未知点不改变分区。它不是可达服务圈，也不判定服务缺口。'}


def neighbors_count(target,points,tree,geoms,radius):
    own=set(target['source_ids'])
    return sum(1 for j in tree.query(target['geometry'].buffer(radius).envelope)
               if not own.intersection(points[int(j)]['source_ids']) and target['geometry'].distance(geoms[int(j)])<=radius)


def station_inventory(points,stations,ctx,center,p):
    inside=[f for f in stations if ctx.scopes['analysis'].covers(f['geometry'])]
    if not inside:raise ValueError('分析区没有通过筛选的来源明确站点，无法生成站点图；不能用普通设施或几何点代替。')
    geoms=[f['geometry'] for f in points];tree=STRtree(geoms);rows=[];shown=[]
    for f in inside:
        n=neighbors_count(f,points,tree,geoms,p['radius_m']);category=STATION_LABELS[f['primary']]
        shown.append(point_record(f,category,neighbor_count=n))
        rows.append(detail(f,nearby_source_records=n,radius_m=p['radius_m']))
    return {'analysis_feature_ids':[f['id'] for f in inside],'display_points':shown,'context_points':[point_record(f) for f in points if ctx.scopes['analysis'].covers(f['geometry'])],
            'detail_table':rows,'table':count_table(shown),'statistic_total':len(inside),'statistic_unit':'个',
            'radius_m':p['radius_m'],'headline':f'{len(inside)} 个','headline_caption':'来源明确站点 · 保留原分类',
            'method':f"站点来自明确的来源站点分类；背景为设施点，符号面积按{p['radius_m']:g}米内其他设施记录数映射，排除自身来源ID。类别不按名称纠正，不编造线路、站点整合、客流或实时运营状态。"}


def concentration_nodes(points,ctx,center,p):
    geoms=[f['geometry'] for f in points];tree=STRtree(geoms)
    candidates=[(neighbors_count(f,points,tree,geoms,p['radius_m']),f) for f in points if ctx.scopes['analysis'].covers(f['geometry'])]
    chosen=[]
    for n,f in sorted(candidates,key=lambda item:(-item[0],item[1]['id'])):
        if all(f['geometry'].distance(other['geometry'])>=p['min_separation_m'] for _,other in chosen):chosen.append((n,f))
        if len(chosen)>=p['max_nodes']:break
    shown=[];rows=[];links=[];circles=[]
    for i,(n,f) in enumerate(chosen):
        category='周边记录 <10' if n<10 else '周边记录 10–29' if n<30 else '周边记录 ≥30'
        shown.append(point_record(f,category,neighbor_count=n,number=i+1))
        rows.append(detail(f,number=i+1,neighbor_count=n,radius_m=p['radius_m'],straight_distance_to_site_m=f['geometry'].distance(center)))
        if not center.equals(f['geometry']):links.append(mapping(LineString([center,f['geometry']])))
        circles.append(mapping(f['geometry'].buffer(p['radius_m']).intersection(ctx.scopes['analysis'])))
    return {'analysis_feature_ids':[f['id'] for _,f in chosen],'display_points':shown,'context_points':[point_record(f) for f in points if ctx.scopes['analysis'].covers(f['geometry'])],
            'relation_lines':links,'influence_circles':circles,'detail_table':rows,'table':count_table(shown),
            'statistic_total':len(chosen),'statistic_unit':'个','candidate_count':len(candidates),'radius_m':p['radius_m'],
            'headline':f'{len(chosen)} 处','headline_caption':'按来源设施集中度与间距筛选的节点',
            'method':f"以真实设施点为候选，按{p['radius_m']:g}米内其他记录数降序、来源ID稳定排序，至少相隔{p['min_separation_m']:g}米选至多{p['max_nodes']}点；圈为统计半径，联系线为项目直线，不表示城市主轴、路网中心性或人流。"}


def analyze_points(did,parameters,resources,ctx,project):
    p={**DEFAULTS[did],**parameters};validate_parameters(did,p,ctx)
    possible={key for rid,r in resources.items() for f in r['features'] for key in (f['id'],f"{rid}/{f['id']}")}
    unknown={str(x) for x in p.get('exclude_ids',[])}-possible
    if unknown:raise ValueError(f'exclude_ids含不存在的来源ID：{sorted(unknown)}')
    points,selection=select_points('pois',resources['pois'],p,ctx);center=center_point(ctx,project)
    funcs={'poitype':source_categories,'sitemap':nearest_index,'poiheat':gaussian_density,'voronoi':euclidean_partition,'urbangrid':point_grid,'nodes':concentration_nodes}
    if did=='urbanstations':
        stations,station_selection=select_points('transit_stops',resources['transit_stops'],p,ctx,filter_categories=False)
        result=station_inventory(points,stations,ctx,center,p);selections=[selection,station_selection]
    else:result=funcs[did](points,ctx,center,p);selections=[selection]
    result.update(algorithm_version=POINTS_VERSION,diagram_id=did,display_kind='point_evidence',point_kind=did,
        analysis_crs=ctx.crs.to_string(),scopes={k:mapping(v) for k,v in ctx.scopes.items()},true_north_vector=ctx.true_north,
        analysis_area_m2=ctx.scopes['analysis'].area,input_feature_count=sum(len(r['features']) for r in resources.values()),
        analysis_feature_count=len(result['analysis_feature_ids']),data_hashes={rid:r['sha256'] for rid,r in resources.items()},
        source=source_summary(resources),selection_audit=selections,total_area_m2=None,total_length_m=None,coverage_percent=None,unmapped_area_m2=None,
        display_features=[],editable_parts='SVG图形与文字可编辑；来源ID、筛选记录和统计明细保留。',center=list(center.coords)[0])
    result['source_point_count_in_analysis']=sum(ctx.scopes['analysis'].covers(f['geometry']) for f in points)
    result['quality_flags']=[{'source_id':f['id'],'name':f['name'],'primary':f['primary'],'warning':f['quality_warning']} for f in points if f['quality_warning']]
    result['filter_configuration']={k:p[k] for k in ('categories','min_confidence','unknown_confidence','deduplicate','exclude_permanently_closed')}
    result['data_sha256']=digest(result['data_hashes'])
    result['display_feature_count']=len(result.get('display_cells',[]))+len(result.get('display_points',[]))
    return result
