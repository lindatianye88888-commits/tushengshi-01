from .catalog import DIAGRAMS, FORMATS, IMPLEMENTED, IMPLEMENTATION_SCOPE, DEFAULTS, dependencies
from .task import load_task, effective_parameters, unwrap, active_cards, is_confirmed
from .spatial import context, load_dataset
from .styles import effective_style, validate_style


def prepare(task_path):
    task = load_task(task_path)
    selected = [i for i in task["diagrams"] if i.get("selected")]
    ctx, spatial_error = None, None
    if selected:
        try:
            ctx = context(task["project"])
        except (ValueError, TypeError, RuntimeError) as exc:
            spatial_error = str(exc)
    resources, resource_errors, instances = {}, {}, []
    for item in selected:
        did, iid = item["diagram_id"], item["instance_id"]
        provenance = effective_parameters(task, item)
        params = {k: unwrap(v) for k, v in provenance.items()}
        implemented = did in IMPLEMENTED
        blockers, data_errors, warnings = [], [], []
        style=effective_style(task,item)
        try:
            validate_style(style)
        except ValueError as exc:
            blockers.append(str(exc))
        confirmed = is_confirmed(task, item)
        if not confirmed:
            blockers.append("需求待确认或确认后发生变更：先查看任务卡，再执行 confirm。")
        else:
            provenance = {k: {**v, "confirmed": True} for k, v in provenance.items()}
        formats = item.get("output_formats", task.get("output_formats", ["svg"]))
        if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats):
            blockers.append("output_formats 仅支持非空的 svg / png / pdf 列表。")
            formats = []
        formats = list(dict.fromkeys(formats))
        if not implemented:
            blockers.append("该图类尚未实现；保留需求，不执行替代算法。")
        else:
            from .statistics import STATISTICAL_DIAGRAMS, validate_parameters
            if did in STATISTICAL_DIAGRAMS:
                try:
                    validate_parameters(did, params, ctx)
                except ValueError as exc:
                    blockers.append(str(exc))
            supported = set(DEFAULTS[did]) | {"exclude_ids"}
            for key, value in params.items():
                if value is not None and key not in supported:
                    if key in {"spatial_context_ref", "data_snapshot_ref"} and value in ("project", None):
                        continue
                    blockers.append(f"首版尚不支持参数 {key}；不会忽略后继续出图。")
            if did == "landuse" and params.get("mode") not in {"parcels", "building_functions"}:
                blockers.append("landuse.mode 需要 parcels 或 building_functions。")
            if did == "landuse" and params.get("overlap_policy") not in {"reject", "smallest_first"}:
                blockers.append("overlap_policy 需要 reject 或 smallest_first。")
            if did == "gw" and params.get("overlap_policy") not in {"reject", "water_priority"}:
                blockers.append("绿水 overlap_policy 需要 reject 或 water_priority。")
            if did == "gw" and (not isinstance(params.get("water_classes"),list) or
                                any(not isinstance(v,str) for v in params["water_classes"])):
                blockers.append("water_classes 需要分类名称的字符串列表。")
            if not isinstance(params.get("class_mapping", {}), dict) or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in
                    params.get("class_mapping", {}).items()):
                blockers.append("class_mapping 需要字符串到字符串的映射。")
            if "classification_property" in params and (
                    not isinstance(params["classification_property"], str) or not params["classification_property"]):
                blockers.append("classification_property 需要非空的属性字段名。")
            for key in ("include_paths", "show_names"):
                if key in params and not isinstance(params[key], bool):
                    blockers.append(f"{key} 需要 true / false。")
            if not isinstance(params.get("exclude_ids", []), list) or any(
                    not isinstance(v, (str, int)) for v in params.get("exclude_ids", [])):
                blockers.append("exclude_ids 需要对象 ID 列表。")
        required = dependencies(did, params)
        if spatial_error:
            data_errors.append(spatial_error)
        for rid in required:
            if not implemented:
                continue
            if rid not in resources and rid not in resource_errors and ctx:
                try:
                    resources[rid] = load_dataset(rid, task.get("datasets", {}).get(rid),task_path.parent,ctx)
                except (ValueError, OSError, TypeError, RuntimeError) as exc:
                    resource_errors[rid] = str(exc)
            if rid in resource_errors:
                data_errors.append(f"{rid}: {resource_errors[rid]}")
            if rid in resources and resources[rid]["metadata"]["estimated_fields"]:
                warnings.append(f"{rid} 含估算字段：" + ", ".join(resources[rid]["metadata"]["estimated_fields"]))
            if rid in resources and resources[rid]["metadata"].get("mapping_completeness") != "verified_complete":
                warnings.append(f"{rid} 仅代表数据源已收录对象，现实完整性未核实；地图空白不等于现实空地。")
        if not implemented:
            warnings.append("依赖列表仅为目录基础依赖；模式相关的完整检查尚未实现。")
        elif did in {"density","orientrose","typology"}:
            warnings.append("当前实现范围："+IMPLEMENTATION_SCOPE[did])
        blockers.extend(data_errors)
        instances.append({"instance_id": iid, "diagram_id": did, "name": DIAGRAMS[did]["name_zh"],
                          "group_id": DIAGRAMS[did]["family"],
                          "requirement_status": "confirmed" if confirmed else "selected",
                          "data_status": ("not_checked" if not implemented else
                                          "needs_input" if data_errors else "ready"),
                          "implementation_status": "implemented" if implemented else "not_implemented",
                          "run_status": "not_started" if implemented else "not_implemented",
                          "parameters": provenance, "required_data": required, "output_formats": formats,
                          "style":style,
                          "blockers": blockers, "warnings": warnings,
                          "acceptance": DIAGRAMS[did]["proposed_acceptance"],
                          "interpretation_boundary": DIAGRAMS[did]["interpretation_boundary"]})
    if any(i["diagram_id"]=="landuse" for i in instances) and any(i["diagram_id"]=="gw" for i in instances):
        for item in instances:
            if item["diagram_id"] in {"landuse","gw"}:
                item["warnings"].append("用地分区与绿水图可能采用不同叠置优先级；跨图比较水域面积时需同时核对方法。")
    nodes = []
    for rid in sorted({r for i in instances for r in i["required_data"]}):
        nodes.append({"id": f"data:{rid}", "kind": "data_check", "depends_on": []})
    for item in instances:
        iid = item["instance_id"]
        nodes.append({"id": f"analyze:{iid}", "kind": "analysis",
                      "depends_on": [f"data:{r}" for r in item["required_data"]]})
        nodes.append({"id": f"render:{iid}", "kind": "render", "depends_on": [f"analyze:{iid}"]})
        nodes.extend({"id": f"export:{iid}:{f}", "kind": "export",
                      "depends_on": [f"render:{iid}"]} for f in item["output_formats"])
    candidates = [i for i in task["diagrams"] if not i.get("selected") and not i.get("rejected")]
    plan = {"schema_version": "0.3.0", "project_name": task["project"].get("name", "未命名项目"),
            "is_example": task.get("is_example", False),
            "counts": {"selected_diagram_types": len({i["diagram_id"] for i in instances}),
                       "selected_instances": len(instances), "candidate_instances": len(candidates),
                       "active_group_cards": len(active_cards(task)),
                       "data_checks": len({r for i in instances for r in i["required_data"]}),
                       "planned_export_files": sum(len(i["output_formats"]) for i in instances),
                       "runnable_instances": sum(not i["blockers"] for i in instances)},
            "instances": instances, "candidates": candidates,
            "task_blockers":[] if selected else ["还没有选中图纸。"],
            "active_groups": [{"group_id": c["group_id"], "name": c["name"],
                               "conditional_fields": c["conditional_fields"],
                               "shared_fields": c["shared_fields"], "acceptance": c["acceptance"]}
                              for c in active_cards(task)],
            "nodes": nodes, "spatial_error": spatial_error,
            "resources": {rid: {"sha256": value["sha256"], "metadata": value["metadata"]}
                          for rid, value in resources.items()}}
    return task, plan, ctx, resources


def task_card_markdown(task, plan):
    lines = [f"# {plan['project_name']} · 出图任务卡", "",
             "操作顺序：检查项目与口径 → 确认所选需求 → 补齐资料 → 执行 → 人工验收。", "",
             f"选中 {plan['counts']['selected_instances']} 个实例；"
             f"启用 {plan['counts']['active_group_cards']} 组；"
             f"候选 {plan['counts']['candidate_instances']} 个；"
             f"可执行 {plan['counts']['runnable_instances']} 个；"
             f"计划导出 {plan['counts']['planned_export_files']} 个文件。", "",
             "## 项目共用信息", "",
             f"- 坐标：{task['project'].get('source_crs')} → {task['project'].get('analysis_crs')}",
             "- 显示、分析、取数范围分别保存在 project.scopes，未覆盖时继承红线。",
             f"- 风格：{task.get('shared_style', {})}", ""]
    if task.get("is_example"):
        lines.extend(["**合成或示例项目，不能用于真实场地结论。**", ""])
    for group in plan["active_groups"]:
        lines.extend([f"## {group['name']}", ""])
        for prompt in group["conditional_fields"]:
            lines.append(f"- 需明确：{prompt['ask']}")
        lines.append("")
        for item in plan["instances"]:
            if item["group_id"] != group["group_id"]:
                continue
            lines.extend([f"### {item['name']} `{item['instance_id']}`", "",
                          f"需求：{item['requirement_status']}；资料：{item['data_status']}；"
                          f"实现：{item['implementation_status']}。", "",
                          f"- 依赖：{', '.join(item['required_data'])}",
                          f"- 格式：{', '.join(item['output_formats'])}"])
            lines.append(f"- 样式：{item['style']}")
            for key, value in item["parameters"].items():
                scope=(f"；继承 {value['inherited_from']}" if value.get("inherited_from") else "")
                override=(f"；覆盖组设置 {value['overrides_group']}" if value.get("overrides_group") else "")
                lines.append(f"- `{key}` = {value['value']}（来源 {value['source']}；确认 {value['confirmed']}{scope}{override}）")
            lines.extend(f"- 待处理：{b}" for b in item["blockers"])
            lines.extend(f"- 提示：{w}" for w in item["warnings"])
            lines.extend([f"- 解读边界：{item['interpretation_boundary']}", "", "验收：", ""])
            lines.extend(f"- [ ] {a}" for a in item["acceptance"])
            lines.append("")
    if plan["candidates"]:
        lines.extend(["## 候选，尚未加入交付", ""])
        lines.extend(f"- {i['instance_id']}：{i.get('recommendation_reason', '待采纳')}"
                     for i in plan["candidates"])
    return "\n".join(lines) + "\n"
