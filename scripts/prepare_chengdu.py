"""Turn retained Overture extracts into a reproducible three-diagram task."""
from collections import Counter
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT))
import pyarrow.parquet as pq
from pyproj import Transformer
from shapely import from_wkb, make_valid
from shapely.geometry import Point, box, mapping
from shapely.ops import transform, unary_union
from workflow.task import new_task, add_instance, confirm, parameter, write_json

PROJECT = ROOT / "projects/chengdu-mixc-5km"
LON, LAT = 104.1126387, 30.6507515

ROAD_CLASSES = {
    "motorway": "高速快速路", "trunk": "高速快速路", "primary": "主干道", "secondary": "次干道",
    "tertiary": "次干道", "residential": "支路街道", "living_street": "支路街道",
    "unclassified": "支路街道", "service": "内部服务路", "footway": "步行与非机动车",
    "path": "步行与非机动车", "pedestrian": "步行与非机动车", "steps": "步行与非机动车",
    "cycleway": "步行与非机动车", "track": "内部服务路", "bridleway": "步行与非机动车",
    "unknown": "未知",
}
LAND_CLASSES = {
    "residential": "居住用地", "commercial": "商业服务", "retail": "商业服务", "education": "公共服务",
    "school": "公共服务", "college": "公共服务", "university": "公共服务", "hospital": "公共服务",
    "medical": "公共服务", "civic": "公共服务", "religious": "公共服务", "cemetery": "公共服务",
    "institutional": "公共服务", "military": "公共服务", "industrial": "工业仓储", "warehouse": "工业仓储",
    "quarry": "工业仓储", "park": "绿地休闲", "recreation_ground": "绿地休闲", "recreation": "绿地休闲",
    "forest": "绿地休闲", "grass": "绿地休闲", "meadow": "绿地休闲", "village_green": "绿地休闲",
    "garden": "绿地休闲", "allotments": "绿地休闲", "nature_reserve": "绿地休闲", "sports": "绿地休闲",
    "railway": "交通设施", "transportation": "交通设施", "parking": "交通设施", "landfill": "其他已知用地",
    "construction": "其他已知用地", "brownfield": "其他已知用地", "farmland": "其他已知用地",
    "farmyard": "其他已知用地", "agriculture": "其他已知用地", "water": "水域",
}


LAND_CLASSES.update({"pitch": "绿地休闲", "track": "绿地休闲", "stadium": "绿地休闲",
                     "playground": "绿地休闲", "flowerbed": "绿地休闲", "pedestrian": "交通设施",
                     "plaza": "交通设施", "traffic_island": "交通设施", "kindergarten": "公共服务"})
LAND_CLASSES["military"] = "其他已知用地"


def extract(kind):
    source = PROJECT / f"data/raw/overture-{kind}.parquet"
    if not source.exists():
        source = PROJECT / f"data/raw/overture-{kind}-aws.parquet"
    if not source.exists():
        return []
    rows = pq.read_table(source).to_pylist()
    features, changes = [], []
    project = Transformer.from_crs(4326, 32648, always_xy=True).transform
    for row in rows:
        if kind == "segment" and row.get("subtype") != "road":
            continue
        geom = from_wkb(row.pop("geometry"))
        if geom.is_empty:
            changes.append({"id": row["id"], "action": "skip_empty"})
            continue
        if not geom.is_valid:
            original_area = geom.area
            geom = make_valid(geom)
            changes.append({"id": row["id"], "action": "make_valid", "area_before_deg2": original_area,
                            "area_after_deg2": geom.area})
        allowed = {"LineString", "MultiLineString"} if kind == "segment" else {"Polygon", "MultiPolygon"}
        if geom.geom_type not in allowed:
            changes.append({"id": row["id"], "action": "skip_geometry_type", "type": geom.geom_type})
            continue
        # STAC/Parquet bbox tests are only a coarse filter: a dateline-crossing
        # feature can have a world-wide bbox while its geometry is far from China.
        aoi = box(104.052, 30.598, 104.174, 30.703)
        if not geom.intersects(aoi):
            changes.append({"id": row["id"], "action": "skip_outside_exact_aoi"})
            continue
        geom = geom.intersection(aoi)
        if geom.is_empty or geom.geom_type not in allowed:
            changes.append({"id": row["id"], "action": "skip_aoi_boundary_touch"})
            continue
        geom = transform(project, geom)
        if not geom.is_valid:
            before = geom.area
            geom = make_valid(geom)
            if geom.geom_type == "GeometryCollection" and kind != "segment":
                geom = unary_union([g for g in geom.geoms if g.geom_type in {"Polygon", "MultiPolygon"}])
            changes.append({"id": row["id"], "action": "make_valid_after_projection",
                            "area_before_m2": before, "area_after_m2": geom.area})
        if geom.is_empty or geom.geom_type not in allowed:
            changes.append({"id": row["id"], "action": "skip_after_projection"})
            continue
        props = {k: v for k, v in row.items() if v is not None and k != "id"}
        names = row.get("names") or {}
        props["name"] = names.get("primary", "")
        raw_class = row.get("class") or "unknown"
        if kind == "segment":
            props["highway"] = raw_class
            props["layer"] = str(row.get("level") or "0")
            props["map_class"] = ROAD_CLASSES.get(raw_class.removesuffix("_link"), "未知")
        elif kind == "land_use":
            props["map_class"] = LAND_CLASSES.get(raw_class, "其他已知用地" if raw_class != "unknown" else "未知")
        elif kind == "water":
            props["map_class"] = "水域"
        features.append({"type": "Feature", "id": row["id"], "properties": props, "geometry": mapping(geom)})
    write_json(PROJECT / f"data/{kind}-cleaning.json", changes)
    print(f"{kind}: {len(features):,} retained; cleaning actions {len(changes)}; "
          f"classes {Counter(f['properties'].get('map_class','building') for f in features)}", flush=True)
    return features


def main():
    PROJECT.mkdir(parents=True, exist_ok=True)
    task = new_task("成都万象城周边 5 km · 三图试跑")
    center = Point(*Transformer.from_crs(4326, 32648, always_xy=True).transform(LON, LAT))
    circle = center.buffer(5000, quad_segs=256)
    acquisition = center.buffer(5500, quad_segs=256)
    task["project"].update(source_crs="EPSG:32648", analysis_crs="EPSG:32648", boundary=mapping(circle),
                            location={"lon": LON, "lat": LAT, "crs": "EPSG:4326",
                                      "source": "OSM way/259719310 成都万象城用地外包框中心"},
                            scopes={"display": mapping(circle), "analysis": mapping(circle),
                                    "acquisition": mapping(acquisition)},
                            radius_m=5000, radius_definition="UTM48N 投影平面半径，非路网距离")
    task["shared_style"]["palette"] = "atlas"
    data = {kind: extract(kind) for kind in ("building", "segment", "land_use", "water")}
    resources = {"buildings": data["building"], "roads": data["segment"],
                 "landuse": data["land_use"]+data["water"]}
    coverage = mapping(transform(Transformer.from_crs(4326,32648,always_xy=True).transform,
                                 box(104.052, 30.598, 104.174, 30.703)))
    for rid, features in resources.items():
        if not features:
            continue
        path = PROJECT / f"data/{rid}.geojson"
        write_json(path, {"type": "FeatureCollection", "features": features})
        task["datasets"][rid] = {
            "path": f"data/{rid}.geojson", "source": "Overture Maps / © OpenStreetMap contributors",
            "source_url": "https://docs.overturemaps.org/attribution/", "license": "ODbL-1.0",
            "acquired_at": "2026-10-25", "data_period": "Overture 2026-10-21.0；各对象原始更新时间不同",
            "crs": "EPSG:32648", "original_crs": "EPSG:4326", "units": "metres",
            "coverage": coverage, "completeness": "source_complete", "mapping_completeness": "unknown",
            "completeness_definition": "仅指指定来源和外包框内提取完整，不表示现实对象全部收录。",
            "estimated_fields": [], "precision_note": "开放地图；部分建筑源自自动提取，未做现场校核。",
        }
    for did in ("fabric", "roads", "landuse"):
        item = add_instance(task, did)
        if did in {"roads", "landuse"}:
            item["parameters"]["classification_property"] = parameter("map_class")
        if did == "roads":
            item["parameters"]["include_paths"] = parameter(True)
        if did == "landuse":
            item["parameters"]["overlap_policy"] = parameter("smallest_first", "agent_proposed_for_preview")
    # The user explicitly delegated these preview choices; they remain in the snapshot.
    confirm(task)
    write_json(PROJECT / "task.json", task)
    write_json(PROJECT / "scope.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "center", "properties": {"name": "成都万象城"}, "geometry": mapping(Point(LON,LAT))},
        {"type": "Feature", "id": "radius-5000", "properties": {"radius_m":5000},
         "geometry": mapping(transform(Transformer.from_crs(32648,4326,always_xy=True).transform,circle))}]})
    print(f"Task saved: {PROJECT / 'task.json'}", flush=True)


if __name__ == "__main__":
    main()
