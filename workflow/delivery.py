from pathlib import Path
import copy
import hashlib
import json
import zipfile

from . import __version__, TITLE
from .catalog import ROOT, read_json
from .integrity import inspect_run, inside, frozen_input
from .render import contact_board
from .spatial import file_hash
from .task import write_json, now


def preview(run):
    run=Path(run).resolve();audit=inspect_run(run)
    if not audit["passed"]: raise ValueError("完整性检查失败："+"; ".join(audit["problems"]))
    manifest=read_json(run/"manifest.json");task=read_json(run/"task.snapshot.json")
    items=[i for i in manifest["instances"] if i["run_status"] in {"needs_review","complete","changes_requested"}]
    results={i["instance_id"]:read_json(inside(run,i["analysis_file"]["path"])) for i in items}
    path=contact_board(task,items,results,run/"preview.png")
    asset={"path":"preview.png","format":"png","sha256":file_hash(path),"kind":"contact_board"}
    manifest["assets"]=[a for a in manifest.get("assets",[]) if a["path"]!="preview.png"]+[asset]
    write_json(run/"manifest.json",manifest)
    return path


def bundle(run,path,include_inputs=True):
    run=Path(run).resolve();path=Path(path).resolve()
    if path.is_relative_to(run): raise ValueError("成果包请放在运行目录外，避免把成果包自身打包。")
    audit=inspect_run(run)
    if not audit["passed"]: raise ValueError("完整性检查失败："+"; ".join(audit["problems"]))
    manifest=read_json(run/"manifest.json");task=copy.deepcopy(read_json(run/"task.snapshot.json"))
    entries={}
    tracked={"manifest.json","task.snapshot.json","plan.json","task-cards.md","report.md"}
    for item in manifest["instances"]:
        tracked.update(record["path"] for record in [item.get("analysis_file"),item.get("statistics_file"),item.get("details_file"),*item.get("files",[])] if record)
    tracked.update(asset["path"] for asset in manifest.get("assets",[]))
    for relative in tracked:
        source=inside(run,relative)
        if source.is_file(): entries["run/"+source.relative_to(run).as_posix()]=source
    if include_inputs:
        for rid,resource in manifest.get("input_resources",{}).items():
            source=frozen_input(run,resource);name=".inputs/"+source.name;entries[name]=source
            task["datasets"][rid]["path"]=name
        required={rid for i in manifest["instances"] if i["run_status"] in {"needs_review","complete","changes_requested"} for rid in i["required_data"]}
        if required-set(manifest.get("input_resources",{})):
            raise ValueError("此旧版本运行没有冻结输入；请先用新执行器复跑后打包。")
    for source in (ROOT/"workflow").rglob("*.py"):
        entries["workflow/"+source.relative_to(ROOT/"workflow").as_posix()]=source
    for name in ("diagrams.json","group-task-cards.json","development-plan.json"): entries["catalog/"+name]=ROOT/"catalog"/name
    for name in ("requirements.txt","requirements-data.txt","WORKFLOW.md","运行工作流.cmd","运行出图.cmd"):
        entries[name]=ROOT/name
    for name in ("implementation-v02.md","validation-v02.md","development-plan.md","validation-v03.md"):
        source=ROOT/"docs"/name
        if source.is_file(): entries["docs/"+name]=source
    for source in (ROOT/"schemas").glob("*.json"): entries["schemas/"+source.name]=source
    for source in (ROOT/"examples").glob("request*.json"): entries["examples/"+source.name]=source
    readme=(f"# {TITLE} · 成果包\n\n"
            f"执行器版本：{__version__}。本次运行状态：{manifest['status']}。\n\n"
            "先看 run/preview.png、逐图成果和 run/report.md；数据来源、时段与估算边界随图保存。\n\n"
            "Windows安装依赖后可双击“运行出图.cmd”复跑；其他操作见WORKFLOW.md。\n\n"
            "具备本地命令工具的 AI 桌面端可阅读 WORKFLOW.md 操作。安装 requirements.txt 后，在解压目录执行：\n\n"
            "```powershell\npython -m workflow doctor\npython -m workflow check task.json\npython -m workflow run task.json --out rerun-output\n```\n\n"
            +("包内含按哈希冻结的输入资料，无需联网重取。\n" if include_inputs else "这是轻量成果包；复跑需另行提供输入资料。\n"))
    payloads={"task.json":json.dumps(task,ensure_ascii=False,indent=2).encode("utf-8"),"README.md":readme.encode("utf-8")}
    records=[{"path":name,"sha256":file_hash(source),"bytes":source.stat().st_size} for name,source in entries.items()]
    records.extend({"path":name,"sha256":hashlib.sha256(data).hexdigest(),"bytes":len(data)} for name,data in payloads.items())
    receipt={"created_at":now(),"workflow_version":__version__,"status":manifest["status"],"includes_inputs":include_inputs,"audit":audit,"files":records}
    payloads["bundle.json"]=json.dumps(receipt,ensure_ascii=False,indent=2).encode("utf-8")
    path.parent.mkdir(parents=True,exist_ok=True);temporary=path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,source in entries.items(): archive.write(source,name)
        for name,data in payloads.items(): archive.writestr(name,data)
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None: raise ValueError("成果包完整性检查失败。")
    temporary.replace(path)
    return {"path":str(path),"sha256":file_hash(path),"bytes":path.stat().st_size,"files":len(entries)+len(payloads),"status":manifest["status"]}
