from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


CATALOG = read_json(ROOT / "catalog/diagrams.json")
DIAGRAMS = {item["id"]: item for item in CATALOG["diagrams"]}
GROUPS = read_json(ROOT / "catalog/group-task-cards.json")["groups"]

# Capabilities belong to this implementation, separate from the research snapshot.
IMPLEMENTED = {"roads", "landuse", "gw", "fabric"}
FORMATS = {"svg", "png", "pdf"}
DEFAULTS = {
    "roads": {"classification_property": "highway", "include_paths": True,
              "show_names": False, "class_mapping": {}},
    "landuse": {"mode": "parcels", "classification_property": "landuse",
                "class_mapping": {}, "overlap_policy": "reject"},
    "gw": {"classification_property": "kind", "class_mapping": {},
           "overlap_policy": "reject", "water_classes": ["water", "水域"]},
    "fabric": {},
}


def dependencies(diagram_id, parameters):
    if diagram_id == "landuse":
        return ["buildings" if parameters.get("mode") == "building_functions" else "landuse"]
    if diagram_id in IMPLEMENTED:
        return {"roads": ["roads"], "gw": ["green_water"], "fabric": ["buildings"]}[diagram_id]
    return DIAGRAMS[diagram_id]["required_data"]
