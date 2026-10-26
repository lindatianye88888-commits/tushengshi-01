import copy
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import uuid

from .catalog import DIAGRAMS, DEFAULTS, GROUPS, read_json

SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def parameter(value, source="user_explicit", confirmed=False, unit=None):
    return {"value": value, "source": source, "confirmed": confirmed, "unit": unit}


def unwrap(value):
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def new_task(name):
    return {
        "schema_version": "0.3.0", "is_example": False,
        "project": {"name": name, "source_crs": None, "analysis_crs": None,
                    "boundary": None, "scopes": {}, "north_rotation_degrees": 0},
        "shared_style": {"palette": "grey_green", "page": "A3", "language": "zh"},
        "output_formats": ["svg", "png", "pdf"],
        "diagrams": [], "group_tasks": [], "datasets": {},
        "rejected_recommendations": [], "input_events": [],
    }


def validate_task(task):
    if not isinstance(task, dict) or not isinstance(task.get("project"), dict):
        raise ValueError("任务需要 project 对象。")
    instances = task.get("diagrams", [])
    if not isinstance(instances, list):
        raise ValueError("diagrams 必须是列表。")
    seen = set()
    for item in instances:
        iid, did = item.get("instance_id", ""), item.get("diagram_id")
        if not SAFE_ID.fullmatch(iid) or iid in seen:
            raise ValueError(f"实例 ID 非法或重复：{iid!r}")
        seen.add(iid)
        if did not in DIAGRAMS:
            raise ValueError(f"未知图类：{did}")
        for field in ("selected", "confirmed"):
            if field in item and not isinstance(item[field], bool):
                raise ValueError(f"{iid}.{field} 必须为 true / false。")
        if not isinstance(item.get("parameters", {}), dict):
            raise ValueError(f"{iid}.parameters 必须是对象。")
        if item.get("group_id", DIAGRAMS[did]["family"]) != DIAGRAMS[did]["family"]:
            raise ValueError(f"{iid} 的 group_id 与图类目录不一致。")
    group_ids = [g.get("group_id") for g in task.get("group_tasks", [])]
    known = {g["group_id"] for g in GROUPS}
    if len(group_ids) != len(set(group_ids)) or any(g not in known for g in group_ids):
        raise ValueError("组级任务卡 ID 未知或重复。")
    if not isinstance(task.get("datasets", {}), dict):
        raise ValueError("datasets 必须是按资源 ID 索引的对象。")
    if not isinstance(task.get("shared_style",{}),dict) or any(not isinstance(i.get("style",{}),dict) for i in instances):
        raise ValueError("shared_style 与逐图 style 必须是对象。")
    return task


def load_task(path):
    return validate_task(read_json(path))


def effective_parameters(task, item):
    values = {k: parameter(v, "suggested_default") for k, v in
              copy.deepcopy(DEFAULTS.get(item["diagram_id"], {})).items()}
    group = next((g for g in task.get("group_tasks", [])
                  if g["group_id"] == DIAGRAMS[item["diagram_id"]]["family"]), {})
    known = {key for settings in DEFAULTS.values() for key in settings}
    for key, value in copy.deepcopy(group.get("shared_parameters", {})).items():
        if key in known and key not in DEFAULTS.get(item["diagram_id"], {}):
            continue
        if isinstance(value, dict) and value.get("applies_to") and item["diagram_id"] not in value["applies_to"]:
            continue
        values[key] = (dict(value, inherited_from=group["group_id"]) if isinstance(value, dict) and "value" in value
                       else dict(parameter(value, "group_setting"), inherited_from=group["group_id"]))
    for key,value in copy.deepcopy(item.get("parameters",{})).items():
        inherited=values.get(key)
        if isinstance(inherited,dict) and inherited.get("inherited_from") and unwrap(inherited)!=unwrap(value):
            value=dict(value,overrides_group=inherited["inherited_from"]) if isinstance(value,dict) and "value" in value else dict(parameter(value),overrides_group=inherited["inherited_from"])
        values[key]=value
    return {k: v if isinstance(v, dict) and "value" in v else
            parameter(v, "legacy_or_file_input", item.get("confirmed", False))
            for k, v in values.items()}


def event(task, kind, **details):
    task.setdefault("input_events", []).append({"at": now(), "type": kind, **details})


def add_instance(task, did, iid=None, proposed=False, reason=None):
    if did not in DIAGRAMS:
        raise ValueError(f"未知图类：{did}")
    if proposed and did in task.get("rejected_recommendations", []):
        raise ValueError(f"已拒绝推荐 {did}；请用 select 明确选择，或先移除拒绝记录。")
    # Repeated selection merges; explicitly naming another ID creates a scenario.
    existing = next((i for i in task["diagrams"] if
                     (i["instance_id"] == iid if iid else i["diagram_id"] == did)), None)
    if existing:
        if existing["diagram_id"] != did:
            raise ValueError("实例 ID 已被其他图类使用。")
        if not proposed:
            existing.update(selected=True, selection_origin="explicit_selection")
            existing.pop("rejected", None)
        return existing
    if iid is None:
        n = 1
        while any(i["instance_id"] == f"{did}-{n:02d}" for i in task["diagrams"]):
            n += 1
        iid = f"{did}-{n:02d}"
    if not SAFE_ID.fullmatch(iid):
        raise ValueError("实例 ID 只允许小写字母、数字、下划线和连字符，并以字母开头。")
    item = {"instance_id": iid, "diagram_id": did, "group_id": DIAGRAMS[did]["family"],
            "selected": not proposed, "confirmed": False,
            "selection_origin": "system_recommendation" if proposed else "explicit_selection",
            "parameters": {}}
    if proposed:
        item["recommendation_reason"] = reason
    task["diagrams"].append(item)
    return item


def get_instance(task, iid):
    item = next((i for i in task["diagrams"] if i["instance_id"] == iid), None)
    if item is None:
        raise ValueError(f"没有实例 {iid}")
    return item


def confirm(task, iid=None):
    selected = [i for i in task["diagrams"] if i.get("selected") and
                (iid is None or i["instance_id"] == iid)]
    if not selected:
        raise ValueError("没有待确认的选中实例。")
    for item in selected:
        item["confirmed"] = True
        item["confirmed_signature"] = requirement_signature(task, item)
    event(task, "requirements_confirmed", instance_ids=[i["instance_id"] for i in selected])


def requirement_signature(task, item):
    """Manual file edits invalidate only the affected requirement confirmation."""
    value = {"project": {k:v for k,v in task["project"].items() if k not in {"name", "description"}},
             "parameters": {k: unwrap(v) for k, v in effective_parameters(task, item).items()
                            if not (item["diagram_id"]=="roads" and k=="show_names")},
             "formats": item.get("output_formats", task.get("output_formats", ["svg"]))}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def is_confirmed(task, item):
    if not item.get("confirmed"):
        return False
    current = requirement_signature(task, item)
    if item.get("confirmed_signature") == current:
        return True
    # Read-only compatibility for confirmations made by v0.1; changes still invalidate them.
    legacy = {"project": task["project"],
              "parameters": {k: unwrap(v) for k,v in effective_parameters(task,item).items()},
              "formats": item.get("output_formats", task.get("output_formats", ["svg"]))}
    return item.get("confirmed_signature") == hashlib.sha256(
        json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def active_cards(task):
    cards = []
    for group in GROUPS:
        selected = [i for i in task["diagrams"] if i.get("selected") and
                    DIAGRAMS[i["diagram_id"]]["family"] == group["group_id"]]
        if selected:
            cards.append({**group, "selected_instances": selected,
                          "conditional_fields": [f for f in group["conditional_fields"]
                                                 if any(i["diagram_id"] in f["diagram_ids"]
                                                        for i in selected)]})
    return cards
