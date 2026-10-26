"""A small request file lets desktop clients avoid authoring GIS geometry."""
from pathlib import Path
import hashlib

from .catalog import ROOT, IMPLEMENTED, read_json
from .task import (load_task,new_task,write_json,add_instance,get_instance,parameter,event,confirm)
from .project import set_scope
from .styles import validate_style


def apply_request(path):
    path=Path(path).resolve();request=read_json(path)
    if not isinstance(request,dict): raise ValueError("request.json 需要对象。")
    allowed={"request_version","project","diagrams","recommendations","remove_instances","style","formats","data","requirements_confirmed","authorization_note","refresh_data"}
    if set(request)-allowed: raise ValueError(f"未知请求字段：{sorted(set(request)-allowed)}")
    if request.get("request_version","0.1.0")!="0.1.0": raise ValueError("暂不支持此 request_version。")
    for key in ("project","data","style"):
        if key in request and not isinstance(request[key],dict): raise ValueError(f"{key} 需要对象。")
    for key in ("diagrams","recommendations","remove_instances"):
        if key in request and not isinstance(request[key],list): raise ValueError(f"{key} 需要列表。")
    for key in ("requirements_confirmed","refresh_data"):
        if key in request and not isinstance(request[key],bool): raise ValueError(f"{key} 需要 true / false。")
    if request.get("requirements_confirmed") and not str(request.get("authorization_note","")).strip():
        raise ValueError("确认需求需记录已有用户授权的 authorization_note。")
    task_path=path.parent/"task.json";token=hashlib.sha256(path.read_bytes()).hexdigest()
    task=load_task(task_path) if task_path.exists() else new_task(request.get("project",{}).get("name","未命名项目"))
    if task.get("request_sha256")==token: return task_path,request
    project=request.get("project",{})
    if set(project)-{"name","center","radius_m","padding_m","purpose"}: raise ValueError("project 含不支持的字段。")
    if project.get("name"): task["project"]["name"]=project["name"]
    if "purpose" in project: task["project"]["description"]=project["purpose"]
    if "center" in project or "radius_m" in project:
        center=project.get("center",task["project"].get("location",{}))
        if center.get("crs","EPSG:4326")!="EPSG:4326": raise ValueError("center 仅接受已核对的 WGS84 / EPSG:4326，不能直接填高德或百度偏移坐标。")
        set_scope(task,lon=center.get("lon"),lat=center.get("lat"),radius_m=project.get("radius_m",task["project"].get("radius_m")),
                  padding_m=project.get("padding_m"),label=center.get("label"))
    for selection in request.get("diagrams",[]):
        selection={"id":selection} if isinstance(selection,str) else selection
        if not isinstance(selection,dict): raise ValueError("diagrams 需要图类ID或图纸对象列表。")
        if not isinstance(selection.get("id"),str) or set(selection)-{"id","instance_id","parameters","style"}:
            raise ValueError("图纸对象需要 id，可附 instance_id / parameters / style。")
        if not isinstance(selection.get("parameters",{}),dict): raise ValueError("parameters 需要对象。")
        item=add_instance(task,selection["id"],selection.get("instance_id"))
        for key,value in selection.get("parameters",{}).items():
            item["parameters"][key]=value if isinstance(value,dict) and "value" in value else parameter(value,"request_input")
        if selection.get("style"):
            validate_style(selection["style"]);item.setdefault("style",{}).update(selection["style"])
    for proposal in request.get("recommendations",[]):
        if not isinstance(proposal,dict) or not isinstance(proposal.get("id"),str) or not proposal.get("reason"):
            raise ValueError("recommendations 需要包含 id / reason 的对象列表。")
        if proposal["id"] in task.get("rejected_recommendations",[]): continue
        add_instance(task,proposal["id"],proposal.get("instance_id"),True,proposal.get("reason"))
    for iid in request.get("remove_instances",[]):
        get_instance(task,iid).update(selected=False,confirmed=False)
    if "style" in request:
        validate_style(request["style"]);task["shared_style"].update(request["style"])
    if "formats" in request:
        from .catalog import FORMATS
        if not isinstance(request["formats"],list) or not request["formats"] or any(f not in FORMATS for f in request["formats"]):
            raise ValueError("formats 仅支持非空 svg / png / pdf 列表。")
        task["output_formats"]=list(dict.fromkeys(request["formats"]))
    task["request_sha256"]=token
    event(task,"request_applied",request_path=str(path),request_sha256=token,authorization_note=request.get("authorization_note"))
    write_json(task_path,task)
    return task_path,request


def start_request(path,progress=None):
    from .compiler import prepare,task_card_markdown
    task_path,request=apply_request(path);task,plan,_,_=prepare(task_path)
    source={"mode":"existing",**request.get("data",{})}
    if set(source)-{"mode","cache","offline","release"}: raise ValueError("data 含不支持的字段。")
    if source.get("mode") not in {"existing","overture"}: raise ValueError("data.mode 支持 existing / overture。")
    if "offline" in source and not isinstance(source["offline"],bool): raise ValueError("data.offline 需要 true / false。")
    needs_data=any(i["data_status"]=="needs_input" for i in plan["instances"] if i["implementation_status"]=="implemented")
    if source["mode"]=="overture" and (needs_data or request.get("refresh_data",False)):
        from .ingest import fetch_task
        cache=Path(source.get("cache",str(ROOT/"cache/overture")))
        if not cache.is_absolute(): cache=task_path.parent/cache
        fetch_task(task_path,cache,offline=source.get("offline",False),release=source.get("release"),progress=progress)
        task=load_task(task_path)
    if request.get("requirements_confirmed") and task.get("confirmed_request_sha256")!=task.get("request_sha256"):
        confirm(task);task["confirmed_request_sha256"]=task["request_sha256"];write_json(task_path,task)
    task,plan,_,_=prepare(task_path)
    review=task_path.parent/"review";review.mkdir(exist_ok=True)
    write_json(review/"plan.json",plan)
    (review/"task-cards.md").write_text(task_card_markdown(task,plan),encoding="utf-8")
    result={"task":str(task_path),"task_cards":str(review/"task-cards.md"),"counts":plan["counts"]}
    if not plan["counts"]["runnable_instances"]:
        result.update(status="needs_input",blockers=[*plan["task_blockers"],*[b for i in plan["instances"] for b in i["blockers"]]])
    else:
        from .engine import execute
        from .delivery import preview
        run,manifest=execute(task_path,task_path.parent/"output")
        result.update(status=manifest["status"],run=str(run),generated_file_count=manifest["generated_file_count"],preview=str(preview(run)))
    write_json(task_path.parent/"request.result.json",result)
    return result
