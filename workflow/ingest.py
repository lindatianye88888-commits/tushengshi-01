"""Normalize public source features into immutable, project-specific snapshots."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import uuid

from pyproj import Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union

from .catalog import IMPLEMENTED, dependencies
from .providers.overture import extract
from .spatial import context, project_geom, file_hash
from .task import load_task, write_json, effective_parameters, unwrap, parameter, event, now

ROAD_CLASSES = {
    "motorway":"高速快速路", "trunk":"高速快速路", "primary":"主干道", "secondary":"次干道",
    "tertiary":"次干道", "residential":"支路街道", "living_street":"支路街道", "unclassified":"支路街道",
    "service":"内部服务路", "track":"内部服务路", "footway":"步行与非机动车", "path":"步行与非机动车",
    "pedestrian":"步行与非机动车", "steps":"步行与非机动车", "cycleway":"步行与非机动车", "bridleway":"步行与非机动车"}
LAND_CLASSES = {
    "residential":"居住用地", "commercial":"商业服务", "retail":"商业服务",
    **{key:"公共服务" for key in ("education","school","college","university","hospital","medical","civic","religious","cemetery","institutional","kindergarten")},
    **{key:"工业仓储" for key in ("industrial","warehouse","quarry")},
    **{key:"绿地休闲" for key in ("park","recreation_ground","recreation","forest","grass","meadow","village_green","garden","allotments","nature_reserve","sports","pitch","track","stadium","playground","flowerbed")},
    **{key:"交通设施" for key in ("railway","transportation","parking","pedestrian","plaza","traffic_island")},
    **{key:"其他已知用地" for key in ("military","landfill","construction","brownfield","farmland","farmyard","agriculture","greenfield","orchard")}}
KINDS = {"buildings":["building"], "roads":["segment"], "landuse":["land_use","water"],
         "green_water":["land_use","water"]}


def normalize(kind, rows, ctx, bbox):
    output, changes = [], []
    aoi=box(*bbox)
    project=Transformer.from_crs(4326,ctx.crs,always_xy=True).transform
    for row in rows:
        if kind=="segment" and row.get("subtype")!="road":
            continue
        geom=from_wkb(row["geometry"])
        fid=str(row["id"])
        allowed={"LineString","MultiLineString"} if kind=="segment" else {"Polygon","MultiPolygon"}
        if geom.is_empty or geom.geom_type not in allowed:
            changes.append({"id":fid,"action":"skip_geometry_type","type":geom.geom_type});continue
        if not geom.is_valid:
            geom=make_valid(geom);changes.append({"id":fid,"action":"make_valid_source"})
        # An antimeridian-spanning bbox may match China while its geometry is in Hawaii.
        if not geom.intersects(aoi):
            changes.append({"id":fid,"action":"skip_outside_exact_aoi"});continue
        geom=geom.intersection(aoi)
        if geom.is_empty or geom.geom_type not in allowed:
            changes.append({"id":fid,"action":"skip_aoi_boundary_touch"});continue
        geom=transform(project,geom)
        if not geom.is_valid:
            before=geom.area
            geom=make_valid(geom)
            if geom.geom_type=="GeometryCollection" and kind!="segment":
                geom=unary_union([g for g in geom.geoms if g.geom_type in allowed])
            changes.append({"id":fid,"action":"make_valid_projected","area_before_m2":before,"area_after_m2":geom.area})
        if geom.is_empty or geom.geom_type not in allowed or not geom.is_valid:
            changes.append({"id":fid,"action":"skip_invalid_after_projection"});continue
        # Keep source attributes and identity; analysis and drawing use derived geometry.
        props={key:value for key,value in row.items() if key not in {"id","geometry"} and value is not None}
        props["name"]=(row.get("names") or {}).get("primary","")
        raw_class=row.get("class") or "unknown"
        if kind=="segment":
            props["highway"]=raw_class;props["layer"]=str(row.get("level") or "0")
            props["map_class"]=ROAD_CLASSES.get(raw_class.removesuffix("_link"),"未知")
        elif kind=="land_use":
            props["map_class"]=LAND_CLASSES.get(raw_class,"其他已知用地" if raw_class!="unknown" else "未知")
        elif kind=="water":
            props["map_class"]="水域"
        elif kind=="building":
            subtype=row.get("subtype")
            uses={"residential":"居住用地","education":"公共服务","medical":"公共服务","civic":"公共服务",
                  "religious":"公共服务","commercial":"商业服务","entertainment":"商业服务","industrial":"工业仓储","transportation":"交通设施"}
            props["map_class"]=uses.get(subtype,"未知")
        output.append({"type":"Feature","id":fid,"properties":props,"geometry":mapping(geom)})
    return output,changes


def source_attributions(features):
    upstream=sorted({s["dataset"] for f in features for s in f["properties"].get("sources",[])
                     if s.get("dataset") and s.get("property","")==""})
    credits=["© OpenStreetMap contributors, Overture Maps Foundation"]
    if "doi:10.5281/zenodo.8174931" in upstream:
        credits.append("Qian Shi et al. (2023), doi:10.5281/zenodo.8174931, CC BY 4.0")
    if any("Microsoft" in name for name in upstream): credits.append("Microsoft Global ML Building Footprints")
    if any("Google" in name for name in upstream): credits.append("Google Open Buildings, CC BY 4.0")
    return upstream,credits


def fetch_task(task_path, cache, *, offline=False, release=None, progress=None):
    task_path=Path(task_path).resolve();original_hash=file_hash(task_path);task=load_task(task_path);ctx=context(task["project"])
    progress=progress or (lambda message: None)
    # A small query-envelope margin accommodates curved projection edges. The
    # declared coverage remains the exact, conservative acquisition polygon.
    bbox=project_geom(ctx.scopes["acquisition"].buffer(20),ctx.crs,4326).bounds
    if bbox[2]-bbox[0]>180: raise ValueError("当前取数不支持跨日期变更线的场地，请提供本地资料。")
    required=set()
    selected=[i for i in task["diagrams"] if i.get("selected") and i["diagram_id"] in IMPLEMENTED]
    for item in selected:
        params={key:unwrap(value) for key,value in effective_parameters(task,item).items()}
        required.update(dependencies(item["diagram_id"],params))
    if not required: raise ValueError("请先选中至少一张已实现的图纸，再获取资料。")
    kinds=sorted({kind for rid in required for kind in KINDS[rid]})
    snapshot=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]
    folder=task_path.parent/"data/snapshots"/snapshot;folder.mkdir(parents=True)
    features,errors,sources={}, {}, {}
    for kind in kinds:
        try:
            table,meta=extract(cache,kind,bbox,release=release,offline=offline,progress=progress)
            release=meta["release"]  # Pin every type in this snapshot to the first resolved release.
            features[kind],cleaning=normalize(kind,table.to_pylist(),ctx,bbox)
            sources[kind]=meta
            write_json(folder/f"{kind}-cleaning.json",cleaning)
            progress(f"{kind}：保留 {len(features[kind]):,} 个对象；处理记录 {len(cleaning)} 条")
        except (ValueError,OSError,RuntimeError) as exc:
            errors[kind]=str(exc)
    updated=[]
    for rid in sorted(required):
        if any(kind in errors for kind in KINDS[rid]): continue
        if rid=="green_water":
            green=[{**f,"properties":{**f["properties"],"kind":"green"}} for f in features["land_use"] if f["properties"]["map_class"]=="绿地休闲"]
            water=[{**f,"properties":{**f["properties"],"kind":"water"}} for f in features["water"]]
            records=green+water
        else:
            records=[f for kind in KINDS[rid] for f in features[kind]]
        ids=[f["id"] for f in records]
        if len(ids)!=len(set(ids)):
            errors[rid]="跨图层发现重复来源ID，停止写入该资源。";continue
        path=folder/f"{rid}.geojson"
        write_json(path,{"type":"FeatureCollection","features":records})
        upstream,credits=source_attributions(records)
        task.setdefault("datasets",{})[rid]={
            "path":str(path.relative_to(task_path.parent)),"source":"Overture Maps Foundation",
            "source_url":"https://docs.overturemaps.org/attribution/","license":"ODbL-1.0",
            "acquired_at":now(),"data_period":f"Overture {release}；各对象原始更新时间不同",
            "release":release,"crs":ctx.crs.to_string(),"original_crs":"EPSG:4326","units":"metres",
            "coverage":mapping(ctx.scopes["acquisition"]),"query_bbox_wgs84":bbox,"completeness":"source_complete",
            "mapping_completeness":"unknown","estimated_fields":[],"verified_empty":not records,
            "attributions":credits,"upstream_datasets":upstream,"snapshot_id":snapshot,
            "sha256":file_hash(path),"geometry_note":"可能包含遥感屋顶投影，非测绘实测基底。" if rid=="buildings" else "",
            "completeness_definition":"仅指指定来源内区域提取完成，不表示现实对象全部收录。"}
        updated.append(rid)
    # Only suggest missing adapter parameters. Explicit choices are retained.
    for item in selected:
        did=item["diagram_id"];params=item.setdefault("parameters",{})
        resolved=effective_parameters(task,item)
        if did in {"roads","landuse"} and resolved["classification_property"].get("source")=="suggested_default":
            params["classification_property"]=parameter("map_class","source_adapter_suggestion")
        if did=="landuse" and "overlap_policy" not in params:
            params["overlap_policy"]=parameter("smallest_first","source_adapter_suggestion")
        if did=="gw":
            params.setdefault("class_mapping",parameter({"green":"绿地休闲","water":"水域"},"source_adapter_suggestion"))
            params.setdefault("overlap_policy",parameter("water_priority","source_adapter_suggestion"))
    summary={"snapshot_id":snapshot,"release":release,"bbox_wgs84":bbox,"updated_resources":updated,
             "errors":errors,"sources":sources,"task_path":str(task_path)}
    write_json(folder/"ingest.json",summary)
    event(task,"data_fetched",snapshot_id=snapshot,resources=updated,errors=errors,release=release)
    if file_hash(task_path)!=original_hash:
        write_json(folder/"task.proposed.json",task)
        raise ValueError(f"取数期间任务已被修改，未覆盖现任务；资料与建议配置保存在 {folder}，可核对后合并。")
    write_json(task_path,task)
    return summary
