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
IMPLEMENTATION_SCOPE = {
    "roads": "按来源等级绘制中心线并统计长度，不构建通行网络。",
    "landuse": "地块用地或建筑功能属性分类；未知保留未知。",
    "gw": "绿地水体分类；支持水域优先去重。",
    "fabric": "二维建筑轮廓与并集面积。",
    "density": "mode=coverage；规则网格轮廓覆盖率，尚不支持容积率。",
    "orientrose": "weighting=length；真实北向、无向中心线按长度加权。",
    "typology": "sort_by=area_desc、same_scale=true；完整轮廓面积排序图录。",
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
}


def dependencies(diagram_id, parameters):
    if diagram_id == "landuse":
        return ["buildings" if parameters.get("mode") == "building_functions" else "landuse"]
    if diagram_id in IMPLEMENTED:
        return {"roads": ["roads"], "gw": ["green_water"], "fabric": ["buildings"],
                "density": ["buildings"], "orientrose": ["roads"], "typology": ["buildings"]}[diagram_id]
    return DIAGRAMS[diagram_id]["required_data"]
