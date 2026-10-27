"""Source-backed cartographic modes; geometry proximity never implies routing."""
import math

from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, LineString, Polygon, mapping
from shapely.ops import unary_union

from .catalog import DEFAULTS
from .spatial import digest
from .statistics import finite_number, parts

CARTOGRAPHY_VERSION = "cartography-0.4.0"
CARTOGRAPHIC_DIAGRAMS = {"context","location","orient","pedestrian","combined","fabricplus","connectionfield"}
SUBTYPES = {"region","county","locality","localadmin","borough"}
ACCESS_COLORS = {"来源明确允许":"#3b7d63","来源明确禁止":"#b86b64","条件待核对":"#d6aa62","通行未知":"#b9bdb5"}


def validate_parameters(did, p):
    if p.get("mode") != DEFAULTS[did]["mode"]:
        raise ValueError(f"{did} 当前仅支持 mode={DEFAULTS[did]['mode']}。")
    if did=="context" and (not isinstance(p.get("boundary_subtype"),str) or p["boundary_subtype"] not in SUBTYPES):
        raise ValueError("boundary_subtype 需为区域、城市或区县层级；本模式不支持国家尺度。")
    if did=="location":
        levels=p.get("boundary_subtypes")
        if not isinstance(levels,list) or not 2<=len(levels)<=4 or any(not isinstance(s,str) or s not in SUBTYPES for s in levels) or len(set(levels))!=len(levels):
            raise ValueError("boundary_subtypes 需要2至4个不同的区域/城市/区县层级。")
    if did=="orient" and p.get("axis_bearing_deg") is not None:
        if not finite_number(p["axis_bearing_deg"]) or not 0<=p["axis_bearing_deg"]<360 or not isinstance(p.get("axis_source"),str) or not p["axis_source"].strip():
            raise ValueError("人工主轴需要[0,360)的 axis_bearing_deg 和明确的 axis_source。")
    if did=="pedestrian" and not isinstance(p.get("include_steps"),bool):
        raise ValueError("include_steps 需要 true / false。")
    if did=="combined":
        layers=p.get("layers")
        if not isinstance(layers,list) or not layers or any(not isinstance(r,str) or r not in {"buildings","roads","green_water"} for r in layers) or len(set(layers))!=len(layers):
            raise ValueError("layers 需为 buildings / roads / green_water 中不重复的非空有序列表。")
    if did=="fabricplus":
        for key,low,high in [("merge_distance_m",0,100),("simplify_m",0,20),("road_clearance_m",.1,30),("preserve_holes_m2",0,100000)]:
            if not finite_number(p.get(key)) or not low<=p[key]<=high:
                raise ValueError(f"{key} 需要[{low},{high}]内的有限数值。")
        if not isinstance(p.get("protect_water"),bool): raise ValueError("protect_water 需要 true / false。")
    if did=="connectionfield":
        for key,low,high in [("k_nearest",1,8),("max_nodes",1,20000)]:
            if not isinstance(p.get(key),int) or isinstance(p[key],bool) or not low<=p[key]<=high: raise ValueError(f"{key} 需要{low}至{high}的整数。")
        if not finite_number(p.get("max_distance_m")) or not 0<p["max_distance_m"]<=2000:
            raise ValueError("max_distance_m 需要(0,2000]米。")


def source_summary(resources):
    metas=[r["metadata"] for r in resources.values()]
    if not metas:
        return {"source":"项目坐标与任务配置","data_period":"任务快照","acquired_at":"本次任务",
                "attributions":["项目坐标与任务配置"],"mapping_completeness":"not_applicable"}
    return {"source":"; ".join(dict.fromkeys(m["source"] for m in metas)),
            "data_period":"; ".join(dict.fromkeys(m["data_period"] for m in metas)),
            "acquired_at":max(m["acquired_at"] for m in metas),
            "attributions":list(dict.fromkeys(a for m in metas for a in m.get("attributions",[m["source"]]))),
            "mapping_completeness":"verified_complete" if all(m.get("mapping_completeness")=="verified_complete" for m in metas) else "unknown"}


def features(resources, rid, p):
    excluded={str(v) for v in p.get("exclude_ids",[])}
    return [f for f in resources[rid]["features"] if f["id"] not in excluded and f"{rid}/{f['id']}" not in excluded]


def clipped_records(records, boundary, category, rid=""):
    output=[]
    for f in records:
        g=f["geometry"].intersection(boundary)
        if g.is_empty or (g.length<=0 if g.geom_type in {"LineString","MultiLineString"} else g.area<=0): continue
        if g.geom_type not in {"LineString","MultiLineString","Polygon","MultiPolygon"}: continue
        output.append({"id":f["id"],"category":category,"resource_id":rid,"geometry":mapping(g),"name":f["properties"].get("name",""),"kind":f["properties"].get('kind')})
    return output


def table_row(category,value,unit):
    return {"category":category,"value":value,"unit":unit,"share_percent":None,"analysis_area_percent":None}


def center_point(ctx,project):
    location=project.get("location",{})
    if "lon" in location and "lat" in location:
        return Point(*Transformer.from_crs(4326,ctx.crs,always_xy=True).transform(location["lon"],location["lat"]))
    return ctx.scopes["analysis"].centroid


def administrative(did,p,resources,ctx,project):
    center=center_point(ctx,project)
    levels=[p["boundary_subtype"]] if did=="context" else p["boundary_subtypes"]
    selected=[]
    for level in levels:
        found=[f for f in features(resources,"context_boundaries",p) if f["properties"].get("subtype")==level and f["geometry"].covers(center)]
        if not found: raise ValueError(f"缺少覆盖项目点的 {level} 行政范围，不能用分析圆圈冒充行政边界。")
        chosen=min(found,key=lambda f:(f["geometry"].area,f["id"]))
        selected.append(chosen)
    panels=[{"source_id":f["id"],"label":f["properties"].get("name") or f["id"],"subtype":f["properties"].get("subtype"),"geometry":mapping(f["geometry"])} for f in selected]
    layers=[]
    if did=="context":
        layers=[{"id":"roads","features":clipped_records(features(resources,"roads",p),ctx.scopes["display"],"道路线","roads")}]
    return {"panels":panels,"center":list(center.coords)[0],"display_layers":layers,
            "analysis_feature_ids":[f['id'] for f in selected],"statistic_total":len(selected),"statistic_unit":"个",
            "table":[table_row("定位行政范围",len(selected),"个")],
            "detail_table":[{"source_id":f["id"],"name":f["properties"].get("name",""),"subtype":f["properties"].get("subtype"),"area_m2_projected":f["geometry"].area} for f in selected],
            "headline":f"{len(selected)} 级" if did=="location" else panels[0]["label"],
            "headline_caption":"公开行政范围定位示意",
            "legend_colors":{"定位行政范围":"#6f917d","道路线":"#9faa9b"},
            "method":"依据来源行政面包含关系定位，各视窗单独使用投影比例尺；大范围为定位示意。道路只显示已取数的本地范围，不补画全城路网。"}


def orientation(p,ctx,project):
    angle=math.degrees(math.atan2(*ctx.true_north))
    axis=p.get("axis_bearing_deg")
    return {"analysis_feature_ids":[],"statistic_total":angle,"statistic_unit":"°",
            "table":[table_row("真北相对网格北偏角",angle,"°")],"display_layers":[],
            "axis_bearing_deg":axis,"axis_source":p.get("axis_source"),"north_grid_angle_deg":angle,
            "headline":f"{angle:+.3f}°","headline_caption":"场地中心真北相对投影网格北",
            "legend_colors":{"真北相对网格北偏角":"#3a6250"},
            "method":"从场地中心纬度微增计算真北向量；主轴只接受有来源的人工方位。分析范围的圆圈不作为建筑或项目主轴。"}


def access_state(props):
    if "access_restrictions" not in props:
        raise ValueError("道路缺少 access_restrictions 字段；旧版缓存不能用于本模式，请重新取数。")
    rules=props.get("access_restrictions") or []
    if not isinstance(rules,list): raise ValueError("access_restrictions 必须是规则列表。")
    unconditional=[];conditional=False
    for rule in rules:
        if not isinstance(rule,dict): raise ValueError("通行规则必须是对象。")
        when=rule.get("when") or {}
        if not isinstance(when,dict): raise ValueError("通行条件 when 必须是对象。")
        when={key:value for key,value in when.items() if value is not None}
        modes=when.get("mode")
        if modes is not None and (not isinstance(modes,list) or any(not isinstance(m,str) for m in modes)):
            raise ValueError("通行模式 mode 必须是字符串列表。")
        if modes is not None and "foot" not in modes: continue
        if rule.get("between") not in (None,[0,1],[0.0,1.0]): conditional=True;continue
        if set(when)-{"mode"}: conditional=True;continue
        value=rule.get("access_type")
        if value in {"allowed","denied"}: unconditional.append(value)
        else: conditional=True
    if conditional: return "条件待核对"
    if len(set(unconditional))>1: return "条件待核对"
    if unconditional: return "来源明确允许" if unconditional[0]=="allowed" else "来源明确禁止"
    return "通行未知"


def pedestrian(p,resources,ctx):
    data=[];details=[];lengths=dict.fromkeys(ACCESS_COLORS,0.0)
    for f in features(resources,"roads",p):
        if not p["include_steps"] and (f["properties"].get("highway") or f["properties"].get("class"))=="steps": continue
        geom=f["geometry"].intersection(ctx.scopes["analysis"])
        if geom.length<=0: continue
        state=access_state(f["properties"])
        lengths[state]+=geom.length
        details.append({"source_id":f["id"],"access_state":state,"source_line_length_m":geom.length})
        data.extend(clipped_records([f],ctx.scopes["display"],state,"roads"))
    return {"analysis_feature_ids":[d["source_id"] for d in details],"display_layers":[{"id":"access","features":data}],
            "statistic_total":sum(lengths.values()),"statistic_unit":"m","total_length_m":sum(lengths.values()),
            "table":[table_row(k,v,"m") for k,v in lengths.items()],"detail_table":details,
            "headline":f"{lengths['来源明确允许']/1000:.2f} km","headline_caption":"来源明确允许步行的线长；未经现场核实",
            "legend_colors":ACCESS_COLORS,"method":"逐道路读取来源通行限制；时段、方向、区段或冲突规则列为条件待核对，无明确规则列为未知；统计来源线长，不构建可达路网。"}


def overlay(p,resources,ctx):
    names={"buildings":"建筑轮廓","roads":"道路线","green_water":"绿地水体"}
    layers=[];details=[];ids=[]
    for rid in p["layers"]:
        records=features(resources,rid,p)
        in_area=[f for f in records if (f["geometry"].intersection(ctx.scopes["analysis"]).length if rid=="roads" else f["geometry"].intersection(ctx.scopes["analysis"]).area)>0]
        ids.extend(f"{rid}/{f['id']}" for f in in_area)
        layers.append({"id":rid,"features":clipped_records(records,ctx.scopes["display"],names[rid],rid)})
        details.append({"layer":rid,"name":names[rid],"source_objects_in_analysis":len(in_area)})
    return {"analysis_feature_ids":ids,"display_layers":layers,"statistic_total":len(layers),"statistic_unit":"层",
            "table":[table_row(names[rid],1,"层") for rid in p["layers"]],"detail_table":details,
            "headline":f"{len(layers)} 层","headline_caption":"同一坐标与范围下的图层叠加",
            "legend_colors":{"建筑轮廓":"#435f50","道路线":"#8b937f","绿地水体":"#b4c6a0","水面":"#88aeb7"},
            "method":"在统一投影上按任务指定的图层顺序裁剪并叠加；各类要素数量分别记录，不把不同量纲相加为综合评分。"}


def generalisation(p,resources,ctx):
    boundary=ctx.scopes["analysis"]
    selected=[f for f in features(resources,"buildings",p) if f["geometry"].intersection(boundary).area>0]
    original=unary_union([f["geometry"].intersection(boundary) for f in selected])
    roads=unary_union([f["geometry"] for f in features(resources,"roads",p)]).buffer(p["road_clearance_m"])
    water=unary_union([f["geometry"] for f in features(resources,"green_water",p)
                       if f["properties"].get("kind") in {"water","水域"}]) if p["protect_water"] else unary_union([])
    holes=[shape_ring for poly in parts(original,"Polygon") for ring in poly.interiors
           if (shape_ring:=Polygon(ring)).area>=p["preserve_holes_m2"]]
    base=original.difference(roads.union(water))
    distance=p["merge_distance_m"]/2
    generalized=base.buffer(distance).buffer(-distance) if distance else base
    generalized=generalized.simplify(p["simplify_m"],preserve_topology=True).intersection(boundary).difference(unary_union([roads,water,*holes]))
    polygons=list(parts(generalized,"Polygon"))
    display=[{"id":str(n),"category":"概化图形","geometry":mapping(g.intersection(ctx.scopes['display']))} for n,g in enumerate(polygons) if g.intersection(ctx.scopes['display']).area>0]
    return {"analysis_feature_ids":[f["id"] for f in selected],"display_layers":[{"id":"generalized","features":display}],
            "statistic_total":generalized.area,"statistic_unit":"m²","total_area_m2":generalized.area,
            "table":[table_row("概化图形",generalized.area,"m²")],"original_area_m2":original.area,
            "generalized_area_m2":generalized.area,"component_count":len(polygons),
            "detail_table":[{"component_id":str(n),"illustrative_area_m2":g.area} for n,g in enumerate(polygons)],
            "headline":f"{len(polygons)} 块","headline_caption":"道路/水面隔断后的概化图形",
            "legend_colors":{"概化图形":"#3e6551"},
            "method":"原始建筑并集在道路走廊和水面隔断下形态学合并、拓扑简化，并保留达到阈值的原院落；图形面积仅用于表达核对，不替代原建筑面积。"}


def connection_field(p,resources,ctx):
    incidences={};road_geoms={};positions={}
    for f in features(resources,"roads",p):
        refs=f["properties"].get("connectors")
        if not isinstance(refs,list): raise ValueError("道路缺少来源 connectors，请重新获取完整道路属性。")
        road_geoms[f['id']]=f['geometry']
        seen=set()
        for ref in refs:
            cid=ref.get("connector_id");at=ref.get("at")
            if not isinstance(cid,str) or not finite_number(at) or not 0<=at<=1: raise ValueError("道路连接点引用无效。")
            if (cid,at) in seen: continue
            seen.add((cid,at))
            arms=incidences.setdefault(cid,{})
            arms[f['id']]=arms.get(f['id'],0)+(1 if at in (0,1) else 2)
            positions.setdefault(cid,[]).append(f['geometry'].interpolate(at,normalized=True))
    nodes=[];details=[];by_id={f['id']:f for f in features(resources,'connectors',p)}
    for cid,refs in incidences.items():
        if cid not in by_id:
            if any(ctx.scopes['analysis'].buffer(-2).contains(point) for point in positions[cid]):
                raise ValueError("研究范围内缺少连接点资料。")
            continue
        point=by_id[cid]['geometry']
        if not ctx.scopes['analysis'].covers(point) or sum(refs.values())<3 or len(refs)<2: continue
        if any(road_geoms[rid].distance(point)>2 for rid in refs):
            raise ValueError(f"来源连接点 {cid} 距其道路线超过2米，需核对几何或裁剪方式。")
        nodes.append({'id':cid,'category':'来源路口','geometry':mapping(point),'degree':sum(refs.values())})
    if len(nodes)>p['max_nodes']: raise ValueError("来源路口超出 max_nodes，缩小范围或明确提高上限；不会静默抽样。")
    points=[by_id[n['id']]['geometry'] for n in nodes];tree=STRtree(points);edges=set()
    for i,point in enumerate(points):
        neighbors=sorted((int(j) for j in tree.query(point.buffer(p['max_distance_m']).envelope) if int(j)!=i and 0<point.distance(points[int(j)])<=p['max_distance_m']),
                         key=lambda j:(point.distance(points[j]),nodes[j]['id']))[:p['k_nearest']]
        details.append({'connector_id':nodes[i]['id'],'degree':nodes[i]['degree'],'neighbors':';'.join(nodes[j]['id'] for j in neighbors)})
        edges.update(tuple(sorted((i,j))) for j in neighbors)
    lines=[{'id':f'{i}:{j}','category':'几何近邻','geometry':mapping(LineString([points[i],points[j]]))} for i,j in sorted(edges)]
    return {'analysis_feature_ids':[n['id'] for n in nodes],'display_layers':[{'id':'neighbors','features':lines},{'id':'junctions','features':nodes}],
            'statistic_total':len(nodes),'statistic_unit':'个','table':[table_row('来源路口',len(nodes),'个')],
            'detail_table':details,'edge_count':len(lines),'headline':f'{len(nodes)} 个','headline_caption':'按共享来源连接点识别的路口',
            'legend_colors':{'来源路口':'#3c6d58','几何近邻':'#b2bda8'},
            'method':f"来源共享连接点按端点1支/中间点2支识别度数至少3的路口；{p['max_distance_m']:g}米内每点取{p['k_nearest']}个几何近邻，去除重复连线。空间相交不自动合并，近邻线不代表实际通行或流量。"}


def analyze_cartography(did,parameters,resources,ctx,project):
    p={**DEFAULTS[did],**parameters};validate_parameters(did,p)
    possible={key for rid,r in resources.items() for f in r['features'] for key in (f['id'],f"{rid}/{f['id']}")}
    missing={str(v) for v in p.get('exclude_ids',[])}-possible
    if missing: raise ValueError(f"exclude_ids 含不存在的来源对象：{sorted(missing)}")
    result={'algorithm_version':CARTOGRAPHY_VERSION,'diagram_id':did,'display_kind':'cartographic','cartographic_kind':did,
            'analysis_crs':ctx.crs.to_string(),'scopes':{k:mapping(v) for k,v in ctx.scopes.items()},'true_north_vector':ctx.true_north,
            'analysis_area_m2':ctx.scopes['analysis'].area,'input_feature_count':sum(len(r['features']) for r in resources.values()),
            'data_hashes':{rid:r['sha256'] for rid,r in resources.items()},'source':source_summary(resources),
            'total_area_m2':None,'total_length_m':None,'coverage_percent':None,'unmapped_area_m2':None,'display_features':[],
            'editable_parts':'SVG图形与文字可编辑；来源ID及统计明细保留。'}
    result['data_sha256']=digest(result['data_hashes'] or {'project':project})
    if did in {'context','location'}: result.update(administrative(did,p,resources,ctx,project))
    elif did=='orient': result.update(orientation(p,ctx,project))
    else: result.update({'pedestrian':pedestrian,'combined':overlay,'fabricplus':generalisation,'connectionfield':connection_field}[did](p,resources,ctx))
    result['analysis_feature_count']=len(result['analysis_feature_ids'])
    result['display_feature_count']=sum(len(layer['features']) for layer in result.get('display_layers',[]))
    return result
