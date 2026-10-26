from pathlib import Path
import hashlib
import math
import re

from .catalog import read_json
from .spatial import file_hash
from .task import write_json, now


def inside(run,path):
    root=Path(run).resolve();target=(root/path).resolve()
    if not target.is_relative_to(root): raise ValueError("运行清单路径超出成果目录。")
    return target


def frozen_input(run,resource):
    sha=resource["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}",sha): raise ValueError("输入哈希格式无效。")
    expected=Path(run).resolve().parent/".inputs"/f"{sha}.geojson"
    if (Path(run)/resource["snapshot_path"]).resolve()!=expected:
        raise ValueError("输入快照路径与其哈希不一致。")
    if not expected.exists() or file_hash(expected)!=sha: raise ValueError("输入快照缺失或哈希不符。")
    return expected


def inspect_run(run):
    run=Path(run).resolve();manifest=read_json(run/"manifest.json")
    problems=[];checked=[]
    for asset in manifest.get("assets",[]):
        try:
            if file_hash(inside(run,asset["path"]))!=asset["sha256"]: problems.append("预览或附加成果被修改。")
        except (OSError,ValueError) as exc: problems.append(str(exc))
    if not (run/"task.snapshot.json").exists() or file_hash(run/"task.snapshot.json")!=manifest["task_sha256"]:
        problems.append("任务快照缺失或被修改。")
    for rid,resource in manifest.get("input_resources",{}).items():
        try: frozen_input(run,resource)
        except (OSError,ValueError) as exc: problems.append(f"{rid}: {exc}")
    for item in manifest["instances"]:
        if item["run_status"] not in {"needs_review","complete","changes_requested"}: continue
        if (set(f["format"] for f in item.get("files",[]))!=set(item["output_formats"]) or
                len(item.get("files",[]))!=len(set(item["output_formats"]))):
            problems.append(f"{item['instance_id']} 的实际导出格式与任务清单不一致。")
        records=[item.get("analysis_file"),item.get("statistics_file"),item.get("details_file"),*item.get("files",[])]
        for record in filter(None,records):
            try:
                path=inside(run,record["path"])
                if not path.exists() or file_hash(path)!=record["sha256"]:
                    problems.append(f"{record['path']} 缺失或被修改。")
            except ValueError as exc: problems.append(str(exc))
        try:
            result=read_json(inside(run,item["analysis_file"]["path"]))
            if result["diagram_id"]!=item["diagram_id"]: problems.append("分析图类与清单不一致。")
            if result["analysis_area_m2"]<=0: problems.append("统计范围面积无效。")
            if result.get("coverage_percent") is not None and not 0 <= result["coverage_percent"] <= 100.000001:
                problems.append("面积覆盖超过100%或低于0%。")
            total=result["total_length_m"] if result.get("total_length_m") is not None else result["total_area_m2"]
            if not math.isclose(sum(row["value"] for row in result["table"]),total,rel_tol=1e-8,abs_tol=1e-6):
                problems.append("统计表合计与图上总指标不一致。")
            for rid in item["required_data"]:
                if rid in manifest.get("input_resources",{}) and result["data_sha256"]!=manifest["input_resources"][rid]["sha256"]:
                    problems.append("分析数据版本与冻结输入不一致。")
            checked.append(item["instance_id"])
        except (ValueError,OSError,KeyError,TypeError) as exc: problems.append(f"分析记录无法核对：{exc}")
    return {"checked_at":now(),"passed":not problems,"checked_instances":checked,"problems":problems,
            "scope":"文件完整性、数据版本与统计合计；不替代视觉核对和现场验证。"}


def review_run(run,reviewer,decision,note,instances=None):
    if not reviewer.strip() or not note.strip(): raise ValueError("验收需要记录 reviewer 和具体 note。")
    if decision not in {"accept","changes_requested"}: raise ValueError("未知验收决定。")
    run=Path(run).resolve();audit=inspect_run(run)
    if not audit["passed"]: raise ValueError("完整性检查未通过："+"; ".join(audit["problems"]))
    manifest=read_json(run/"manifest.json");selected=[]
    for item in manifest["instances"]:
        if instances is not None and item["instance_id"] not in instances: continue
        if item["run_status"] not in {"needs_review","complete","changes_requested"}: continue
        selected.append(item["instance_id"])
        item["run_status"]="complete" if decision=="accept" else "changes_requested"
    if not selected or instances is not None and set(instances)-set(selected):
        raise ValueError("所指定实例没有可验收的成果。")
    manifest.setdefault("reviews",[]).append({"at":now(),"reviewer":reviewer,"decision":decision,
        "note":note,"instances":selected,"audit":audit,"scope":"成果核对，不表示现场实测或法定审查。"})
    states={item["run_status"] for item in manifest["instances"]}
    manifest["status"]=("complete" if states=={"complete"} else "changes_requested" if "changes_requested" in states
                         else "needs_review" if states<={"needs_review","complete"} else "incomplete")
    write_json(run/"manifest.json",manifest)
    from .engine import report
    (run/"report.md").write_text(report(manifest),encoding="utf-8")
    return manifest
