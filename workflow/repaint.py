import copy
from datetime import datetime, timezone
from pathlib import Path
import shutil

from . import __version__
from .catalog import read_json
from .engine import finish_run
from .integrity import inspect_run, inside, frozen_input
from .render import export, RENDER_VERSION
from .spatial import file_hash
from .styles import validate_style
from .task import write_json, now


def repaint(run,patch,output_root=None,instances=None,title=None):
    validate_style({"palette":"grey_green","page":"A3","language":"zh",**patch})
    run=Path(run).resolve();audit=inspect_run(run)
    if not audit["passed"]: raise ValueError("原成果完整性检查失败："+"; ".join(audit["problems"]))
    manifest=copy.deepcopy(read_json(run/"manifest.json"));task=read_json(run/"task.snapshot.json")
    renderable=[i for i in manifest["instances"] if i["run_status"] in {"needs_review","complete","changes_requested"}]
    if not renderable or instances and set(instances)-{i["instance_id"] for i in renderable}:
        raise ValueError("没有所指定的可重绘实例。")
    output_root=Path(output_root or run.parent).resolve();output_root.mkdir(parents=True,exist_ok=True)
    target=output_root/datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir()
    if title is not None: task["project"]["name"]=title
    for item in task["diagrams"]:
        if instances is None or item["instance_id"] in instances:
            item["style"]={**item.get("style",{}),**patch}
    for resource in manifest.get("input_resources",{}).values():
        source=frozen_input(run,resource);destination=output_root/".inputs"/source.name
        destination.parent.mkdir(exist_ok=True)
        if source!=destination and (not destination.exists() or file_hash(destination)!=resource["sha256"]):
            shutil.copy2(source,destination)
    for item in renderable:
        item_dir=target/item["instance_id"];item_dir.mkdir()
        for key in ("analysis_file","statistics_file","details_file"):
            if item.get(key):
                source=inside(run,item[key]["path"]);shutil.copy2(source,target/item[key]["path"])
        changed=instances is None or item["instance_id"] in instances or title is not None
        item["analysis_cache_hit"]=True;item["analysis_reused_from"]=str(run)
        if changed:
            item.pop("inherited_reviews",None);item.pop("review_inherited_from",None)
            item["style"]={**item.get("style",task.get("shared_style",{})),**patch} if instances is None or item["instance_id"] in instances else item["style"]
            result=read_json(target/item["analysis_file"]["path"])
            paths=export(result,task,item,item_dir)
            item["files"]=[{"path":str(path.relative_to(target)),"format":path.suffix[1:],"sha256":file_hash(path)} for path in paths]
            item["run_status"]="needs_review"
        else:
            for record in item["files"]: shutil.copy2(inside(run,record["path"]),target/record["path"])
            item["review_inherited_from"]=str(run)
            item["inherited_reviews"]=[*item.get("inherited_reviews",[]),
                *[review for review in manifest.get("reviews",[]) if item["instance_id"] in review["instances"]]]
    write_json(target/"task.snapshot.json",task)
    manifest.update(workflow_version=__version__,run_id=target.name,created_at=now(),task_sha256=file_hash(target/"task.snapshot.json"),
                    project=task["project"],based_on=str(run),render_version=RENDER_VERSION,reviews=[],assets=[])
    if (run/"plan.json").exists(): shutil.copy2(run/"plan.json",target/"plan.json")
    finish_run(target,manifest)
    return target,manifest
