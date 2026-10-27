from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


CATALOG = read_json(ROOT / "catalog/diagrams.json")
DIAGRAMS = {item["id"]: item for item in CATALOG["diagrams"]}
GROUPS = read_json(ROOT / "catalog/group-task-cards.json")["groups"]

# Capabilities belong to this implementation, separate from the research snapshot.
IMPLEMENTED = {"roads", "landuse", "gw", "fabric", "density", "orientrose", "typology"}
IMPLEMENTED.update({"context","location","orient","pedestrian","combined","fabricplus","connectionfield"})
POINT_DIAGRAMS = {"poitype","sitemap","poiheat","voronoi","urbangrid","urbanstations","nodes"}
IMPLEMENTED.update(POINT_DIAGRAMS)
IMPLEMENTATION_SCOPE = {
    "roads": "按来源等级绘制中心线并统计长度，不构建通行网络。",
    "landuse": "地块用地或建筑功能属性分类；未知保留未知。",
    "gw": "绿地水体分类；支持水域优先去重。",
    "fabric": "二维建筑轮廓与并集面积。",
    "density": "mode=coverage；规则网格轮廓覆盖率，尚不支持容积率。",
    "orientrose": "weighting=length；真实北向、无向中心线按长度加权。",
    "typology": "sort_by=area_desc、same_scale=true；完整轮廓面积排序图录。",
    "context": "administrative_context；真实行政范围与本地范围两级表达，道路仅显示已取数区域。",
    "location": "nested_boundaries；已提供的区域/城市/区县行政范围定位，不支持国家尺度。",
    "orient": "true_north；真北参考及有来源的人工方位，不从圆形分析范围推断主轴。",
    "pedestrian": "source_access；来源通行属性底图，未知与条件限制单列，不计算可达路网。",
    "combined": "layer_overlay；对齐并叠加所选原始图层，不生成综合评分。",
    "fabricplus": "road_water_barriers；道路水面隔断下的轮廓概化，仅用于表达；尚无街名标注。",
    "connectionfield": "source_connectors；按来源连接点识别路口、绘制几何近邻，不推断流量。",
    "poitype": "source_categories；来源taxonomy分类及保守去重，不保证现实设施齐全。",
    "sitemap": "nearest_index；按项目点直线距离选取设施，名称/编号索引，不是步行距离。",
    "poiheat": "gaussian_kde；带取数缓冲的截断高斯核密度，单位来源记录/km²，不是人流热力。",
    "voronoi": "euclidean_partition；包含取数缓冲内竞争点的最近点分区，共址设施共用一格；边缘未保证区域单列，不判定服务缺口。",
    "urbangrid": "point_count；固定米制网格点数及有效面积密度，保留零格。",
    "urbanstations": "source_stations；来源明确站点与设施背景，统计周边设施，不推断线路、客流或站点重要性。",
    "nodes": "local_concentration；按周边设施记录数和最小间距选点，项目联系线为直线，不表示城市主轴或路网中心性。",
}
FORMATS = {"svg", "png", "pdf"}
DEFAULTS = {
    "roads": {"classification_property": "highway", "include_paths": True,
              "show_names": False, "class_mapping": {}},
    "landuse": {"mode": "parcels", "classification_property": "landuse",
                "class_mapping": {}, "overlap_policy": "reject"},
    "gw": {"classification_property": "kind", "class_mapping": {},
           "overlap_policy": "reject", "water_classes": ["water", "水域"]},
    "fabric": {},
    "density": {"mode": "coverage", "grid_size_m": 250.0},
    "orientrose": {"bin_count": 18, "weighting": "length", "fold_opposite": True, "include_paths": False},
    "typology": {"sort_by": "area_desc", "max_buildings": 24, "min_area_m2": 20.0, "same_scale": True},
    "context": {"mode":"administrative_context","boundary_subtype":"county"},
    "location": {"mode":"nested_boundaries","boundary_subtypes":["region","county","localadmin"]},
    "orient": {"mode":"true_north","axis_bearing_deg":None,"axis_source":None},
    "pedestrian": {"mode":"source_access","include_steps":True},
    "combined": {"mode":"layer_overlay","layers":["green_water","roads","buildings"]},
    "fabricplus": {"mode":"road_water_barriers","merge_distance_m":10.0,"simplify_m":1.0,
                   "road_clearance_m":3.0,"preserve_holes_m2":200.0,"protect_water":True},
    "connectionfield": {"mode":"source_connectors","k_nearest":3,"max_distance_m":250.0,"max_nodes":10000},
}
_POINT_FILTERS = {"categories":[],"min_confidence":0.5,"unknown_confidence":"include","deduplicate":True,"exclude_permanently_closed":True}
DEFAULTS.update({
    "poitype": {**_POINT_FILTERS,"mode":"source_categories"},
    "sitemap": {**_POINT_FILTERS,"mode":"nearest_index","max_labels":12},
    "poiheat": {**_POINT_FILTERS,"mode":"gaussian_kde","cell_size_m":100.0,"bandwidth_m":150.0,"truncate_sigma":3.0},
    "voronoi": {**_POINT_FILTERS,"mode":"euclidean_partition","max_sites":10000},
    "urbangrid": {**_POINT_FILTERS,"mode":"point_count","cell_size_m":250.0},
    "urbanstations": {**_POINT_FILTERS,"mode":"source_stations","radius_m":300.0},
    "nodes": {**_POINT_FILTERS,"mode":"local_concentration","radius_m":300.0,"min_separation_m":500.0,"max_nodes":12},
})


def dependencies(diagram_id, parameters):
    if diagram_id in POINT_DIAGRAMS:
        return ["pois","transit_stops"] if diagram_id=="urbanstations" else ["pois"]
    if diagram_id=="context": return ["context_boundaries","roads"]
    if diagram_id=="location": return ["context_boundaries"]
    if diagram_id=="orient": return []
    if diagram_id=="pedestrian": return ["roads"]
    if diagram_id=="combined":
        layers=parameters.get("layers",[])
        return [rid for rid in layers if isinstance(rid,str) and rid in {"buildings","roads","green_water"}] if isinstance(layers,list) else []
    if diagram_id=="fabricplus": return ["buildings","roads"]+(["green_water"] if parameters.get("protect_water") else [])
    if diagram_id=="connectionfield": return ["roads","connectors"]
    if diagram_id == "landuse":
        return ["buildings" if parameters.get("mode") == "building_functions" else "landuse"]
    if diagram_id in IMPLEMENTED:
        return {"roads": ["roads"], "gw": ["green_water"], "fabric": ["buildings"],
                "density": ["buildings"], "orientrose": ["roads"], "typology": ["buildings"]}[diagram_id]
    return DIAGRAMS[diagram_id]["required_data"]
