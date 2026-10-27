import copy
import csv
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .analysis import analyze_resources, algorithm_version
from .catalog import read_json
from .compiler import prepare, task_card_markdown
from .spatial import digest, file_hash
from .task import write_json, now
from .project import spatial_signature


def freeze_resources(resources,output_root):
    folder=Path(output_root)/".inputs";folder.mkdir(exist_ok=True)
    records={}
    for rid,data in resources.items():
        target=folder/f"{data['sha256']}.geojson"
        if not target.exists() or file_hash(target)!=data["sha256"]:
            temp=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp");temp.write_bytes(data["raw_bytes"]);temp.replace(target)
        records[rid]={"sha256":data["sha256"],"metadata":data["metadata"],
                      "snapshot_path":f"../.inputs/{target.name}"}
    return records


def write_analysis_files(result,item_dir):
    metric_path=item_dir/"analysis.json";write_json(metric_path,result)
    table_path=item_dir/"statistics.csv"
    with table_path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=["category","value","unit","share_percent","analysis_area_percent"])
        writer.writeheader();writer.writerows(result["table"])
    detail_path = None
    if result.get("detail_table"):
        detail_path = item_dir/"details.csv"
        with detail_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result["detail_table"][0]))
            writer.writeheader();writer.writerows(result["detail_table"])
    return metric_path,table_path,detail_path


def execute(task_path, output_root):
    from .render import export, RENDER_VERSION
    task_path = Path(task_path).resolve()
    task, plan, ctx, resources = prepare(task_path)
    if not plan["instances"]:
        raise ValueError("还没有选中图纸，不能执行空任务。")
    output_root = Path(output_root).resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    cache_dir = output_root / ".cache"
    cache_dir.mkdir(exist_ok=True)
    write_json(run_dir / "task.snapshot.json", task)
    write_json(run_dir / "plan.json", plan)
    (run_dir / "task-cards.md").write_text(task_card_markdown(task, plan), encoding="utf-8")
    manifest = {"workflow_version": __version__, "run_id": run_id, "created_at": now(),
                "task_path": str(task_path), "task_sha256": file_hash(run_dir / "task.snapshot.json"),
                "project": task["project"], "is_example": task.get("is_example", False),
                "status": "running", "instances": [], "generated_file_count": 0,
                "input_resources":freeze_resources(resources,output_root),"render_version":RENDER_VERSION}
    for planned in plan["instances"]:
        item = copy.deepcopy(planned)
        item["files"] = []
        if item["blockers"]:
            item["run_status"] = "not_implemented" if item["implementation_status"] == "not_implemented" else "not_started"
            manifest["instances"].append(item)
            continue
        try:
            item_dir = run_dir / item["instance_id"]
            item_dir.mkdir()
            params = {k: v["value"] for k, v in item["parameters"].items()}
            inputs = {rid: resources[rid] for rid in item["required_data"]}
            key = digest({"algorithm": algorithm_version(item["diagram_id"]), "diagram": item["diagram_id"],
                          "parameters": {k:v for k,v in params.items() if k!="show_names"},
                          "project": spatial_signature(task["project"]),
                          "data_hash": {rid:data["sha256"] for rid,data in inputs.items()},
                          "data_metadata": {rid:data["metadata"] for rid,data in inputs.items()},
                          "location": {k:task["project"].get("location",{}).get(k) for k in ("lon","lat")}})
            cache_path = cache_dir / f"{key}.json"
            try:
                cached = read_json(cache_path) if cache_path.exists() else None
            except (ValueError,OSError):
                cached = None
            if cached and cached.get("result_sha256") == digest(cached.get("result")):
                result = cached["result"]
                item["analysis_cache_hit"] = True
            else:
                result = analyze_resources(item["diagram_id"], params, inputs, ctx, task["project"])
                write_json(cache_path, {"result": result, "result_sha256": digest(result)})
                item["analysis_cache_hit"] = False
            item["analysis_key"] = key
            metric_path,table_path,detail_path = write_analysis_files(result,item_dir)
            item["analysis_file"] = {"path": str(metric_path.relative_to(run_dir)), "sha256": file_hash(metric_path)}
            item["statistics_file"]={"path":str(table_path.relative_to(run_dir)),"sha256":file_hash(table_path)}
            if detail_path:
                item["details_file"]={"path":str(detail_path.relative_to(run_dir)),"sha256":file_hash(detail_path)}
            item["run_status"] = "computed"
            paths = export(result, task, item, item_dir)
            for path in paths:
                item["files"].append({"path": str(path.relative_to(run_dir)),
                                      "format": path.suffix[1:], "sha256": file_hash(path)})
            item["run_status"] = "needs_review"
        except Exception as exc:
            item["run_status"] = "failed"
            item["error"] = f"{type(exc).__name__}: {exc}"
        manifest["instances"].append(item)
        write_json(run_dir / "manifest.json", manifest)
    finish_run(run_dir,manifest)
    return run_dir,manifest


def finish_run(run_dir,manifest):
    manifest["generated_file_count"] = sum(len(i["files"]) for i in manifest["instances"])
    states={i["run_status"] for i in manifest["instances"]}
    manifest["status"] = ("complete" if states=={"complete"} else
                          "needs_review" if states<={"needs_review","complete"} else "incomplete")
    manifest["finished_at"] = now()
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "report.md").write_text(report(manifest), encoding="utf-8")


def report(manifest):
    lines = [f"# {manifest['project'].get('name')} · 运行报告", "",
             f"状态：{manifest['status']}；成果文件：{manifest['generated_file_count']}。", "",
             "配置、数据哈希、算法版本和导出哈希保存在 manifest.json 与逐图 analysis.json。", ""]
    for item in manifest["instances"]:
        lines.extend([f"## {item['name']} / {item['instance_id']}", "",
                      f"状态：{item['run_status']}；复用计算：{item.get('analysis_cache_hit', False)}。", ""])
        lines.extend(f"- [{f['format']}]({f['path'].replace(chr(92), '/')})" for f in item["files"])
        if item.get("statistics_file"):
            lines.append(f"- [统计表 CSV]({item['statistics_file']['path'].replace(chr(92), '/')})")
        if item.get("details_file"):
            lines.append(f"- [计算明细 CSV]({item['details_file']['path'].replace(chr(92), '/')})")
        if item.get("inherited_reviews"):
            lines.extend(f"- 沿用核对记录：{review['reviewer']} / {review['decision']} / {review['note']}" for review in item["inherited_reviews"])
        lines.extend(f"- 待处理：{b}" for b in item["blockers"])
        if item.get("error"):
            lines.append(f"- 失败：{item['error']}")
        lines.extend(f"- 数据提示：{w}" for w in item["warnings"])
        lines.extend(["", f"解读边界：{item['interpretation_boundary']}", "", "验收：", ""])
        lines.extend(f"- [ ] {a}" for a in item["acceptance"])
        lines.append("")
    if manifest.get("reviews"):
        lines.extend(["## 核对记录",""])
        for review in manifest["reviews"]:
            lines.extend([f"- {review['at']} / {review['reviewer']} / {review['decision']}",
                          f"- 涉及实例：{', '.join(review['instances'])}",f"- 事项：{review['note']}",
                          f"- 范围：{review.get('scope','成果核对')}",""])
    return "\n".join(lines)
