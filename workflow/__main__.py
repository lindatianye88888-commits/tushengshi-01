import argparse
import json
from pathlib import Path
import sys

from . import TITLE
from .catalog import DIAGRAMS, IMPLEMENTED, IMPLEMENTATION_SCOPE, DEFAULTS, FORMATS, read_json
from .task import load_task, write_json, new_task, add_instance, confirm, event, get_instance, parameter


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor",help="检查本地依赖与出图环境")
    start=sub.add_parser("start",help="读取跨桌面端的request.json并执行")
    start.add_argument("request",type=Path)
    catalog=sub.add_parser("catalog", help="列出50类图纸与当前实现状态")
    catalog.add_argument("--json",action="store_true")
    init = sub.add_parser("init")
    init.add_argument("task", type=Path)
    init.add_argument("--name", required=True)
    init.add_argument("--lon",type=float)
    init.add_argument("--lat",type=float)
    init.add_argument("--radius-m",type=float)
    init.add_argument("--padding-m",type=float)
    init.add_argument("--label")
    init.add_argument("--diagrams",nargs="+",choices=DIAGRAMS)
    init.add_argument("--formats",nargs="+",choices=sorted(FORMATS))
    init.add_argument("--palette",choices=["atlas","grey_green","monochrome"],default="atlas")
    p=sub.add_parser("scope")
    p.add_argument("task",type=Path)
    p.add_argument("--lon",required=True,type=float)
    p.add_argument("--lat",required=True,type=float)
    p.add_argument("--radius-m",required=True,type=float)
    p.add_argument("--padding-m",type=float)
    p.add_argument("--label")
    p=sub.add_parser("fork")
    p.add_argument("task",type=Path)
    p.add_argument("destination",type=Path)
    p.add_argument("--name")
    p=sub.add_parser("style")
    p.add_argument("task",type=Path)
    p.add_argument("--palette",choices=["atlas","grey_green","monochrome"])
    p.add_argument("--page",choices=["A3","A4"])
    p.add_argument("--instance")
    p=sub.add_parser("fetch")
    p.add_argument("task",type=Path)
    p.add_argument("--cache",type=Path,default=Path("cache/overture"))
    p.add_argument("--release")
    p.add_argument("--offline",action="store_true")
    for command in ("select", "propose"):
        p = sub.add_parser(command)
        p.add_argument("task", type=Path)
        p.add_argument("diagram", choices=DIAGRAMS)
        p.add_argument("--instance")
        if command == "propose":
            p.add_argument("--reason", required=True)
    for command in ("reject", "remove", "confirm"):
        p = sub.add_parser(command)
        p.add_argument("task", type=Path)
        p.add_argument("--instance", required=command != "confirm")
    p = sub.add_parser("set")
    p.add_argument("task", type=Path)
    p.add_argument("instance")
    p.add_argument("key")
    p.add_argument("value", help="JSON 值，例如 true、100、或带引号的字符串")
    p.add_argument("--source",default="user_explicit",choices=["user_explicit","user_adopted","agent_suggestion"])
    for command in ("check", "plan"):
        p = sub.add_parser(command)
        p.add_argument("task", type=Path)
        p.add_argument("--out", type=Path)
        p.add_argument("--json",action="store_true")
    p = sub.add_parser("run")
    p.add_argument("task", type=Path)
    p.add_argument("--out", type=Path, default=Path("output"))
    for command in ("audit","preview"):
        p=sub.add_parser(command);p.add_argument("run",type=Path)
    p=sub.add_parser("review")
    p.add_argument("run",type=Path)
    p.add_argument("--reviewer",required=True)
    p.add_argument("--decision",required=True,choices=["accept","changes_requested"])
    p.add_argument("--note",required=True)
    p.add_argument("--instance",action="append")
    p=sub.add_parser("repaint")
    p.add_argument("run",type=Path)
    p.add_argument("--palette",choices=["atlas","grey_green","monochrome"])
    p.add_argument("--page",choices=["A3","A4"])
    p.add_argument("--title")
    p.add_argument("--instance",action="append")
    p.add_argument("--out",type=Path)
    p=sub.add_parser("bundle")
    p.add_argument("run",type=Path)
    p.add_argument("--out",type=Path)
    p.add_argument("--without-inputs",action="store_true")
    args = parser.parse_args()
    try:
        if args.command=="start":
            from .request import start_request
            try:
                result=start_request(args.request,progress=lambda message:print(message,file=sys.stderr,flush=True))
            except (ValueError,OSError,TypeError,RuntimeError,ImportError) as exc:
                write_json(args.request.resolve().parent/"request.result.json",{"status":"failed","error":str(exc)})
                raise
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result["status"] in {"needs_review","complete"} else 2
        if args.command=="doctor":
            from .doctor import diagnose
            result=diagnose();print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["can_render"] else 2
        if args.command == "catalog":
            if args.json:
                print(json.dumps([{ "id":did,"name":d["name_zh"],"group_id":d["family"],
                                   "implemented":did in IMPLEMENTED,
                                   "implementation_scope":IMPLEMENTATION_SCOPE.get(did),
                                   "supported_parameters":DEFAULTS.get(did,{})} for did,d in DIAGRAMS.items()],ensure_ascii=False))
                return 0
            for did, d in DIAGRAMS.items():
                print(f"{did:20} {d['name_zh']}  {'可运行' if did in IMPLEMENTED else '待开发'}")
            return 0
        if args.command == "init":
            if args.task.exists():
                raise ValueError("任务文件已存在，请另选文件名。")
            task=new_task(args.name);task["shared_style"]["palette"]=args.palette
            if any(v is not None for v in (args.lon,args.lat,args.radius_m)):
                if any(v is None for v in (args.lon,args.lat,args.radius_m)):
                    raise ValueError("范围初始化需要同时提供 --lon --lat --radius-m。")
                from .project import set_scope
                set_scope(task,lon=args.lon,lat=args.lat,radius_m=args.radius_m,padding_m=args.padding_m,label=args.label)
            for did in args.diagrams or []: add_instance(task,did)
            if args.formats: task["output_formats"]=list(dict.fromkeys(args.formats))
            write_json(args.task,task)
        elif args.command=="scope":
            from .project import set_scope
            task=load_task(args.task)
            set_scope(task,lon=args.lon,lat=args.lat,radius_m=args.radius_m,padding_m=args.padding_m,label=args.label)
            write_json(args.task,task)
        elif args.command=="fork":
            if args.destination.exists(): raise ValueError("目标任务已经存在。")
            task=load_task(args.task)
            for dataset in task.get("datasets",{}).values():
                dataset["path"]=str((args.task.resolve().parent/dataset["path"]).resolve())
            if args.name: task["project"]["name"]=args.name
            event(task,"forked",source_task=str(args.task.resolve()))
            write_json(args.destination,task);print(f"已保存：{args.destination.resolve()}");return 0
        elif args.command=="style":
            task=load_task(args.task);patch={k:getattr(args,k) for k in ("palette","page") if getattr(args,k) is not None}
            if not patch: raise ValueError("至少提供一个样式设置。")
            target=(get_instance(task,args.instance).setdefault("style",{}) if args.instance else task.setdefault("shared_style",{}))
            before=dict(target);target.update(patch)
            event(task,"style_changed",instance_id=args.instance,before=before,after=dict(target))
            write_json(args.task,task)
        elif args.command=="fetch":
            from .ingest import fetch_task
            result=fetch_task(args.task,args.cache,offline=args.offline,release=args.release,progress=lambda message:print(message,file=sys.stderr,flush=True))
            print(json.dumps(result,ensure_ascii=False,indent=2));return 2 if result["errors"] else 0
        elif args.command in {"select", "propose", "reject", "remove", "confirm", "set"}:
            task = load_task(args.task)
            if args.command in {"select", "propose"}:
                item = add_instance(task, args.diagram, args.instance, args.command == "propose", getattr(args, "reason", None))
                event(task, args.command, instance_id=item["instance_id"])
            elif args.command == "confirm":
                confirm(task, args.instance)
            else:
                item = get_instance(task, args.instance)
                if args.command == "set":
                    item.setdefault("parameters", {})[args.key] = parameter(json.loads(args.value),args.source)
                else:
                    item["selected"] = False
                    item["confirmed"] = False
                    if args.command == "reject":
                        item["rejected"] = True
                        task.setdefault("rejected_recommendations", []).append(item["diagram_id"])
                event(task, args.command, instance_id=item["instance_id"])
            write_json(args.task, task)
        elif args.command in {"check", "plan"}:
            from .compiler import prepare, task_card_markdown
            task, plan, _, _ = prepare(args.task.resolve())
            out = args.out or args.task.parent / "review"
            out.mkdir(parents=True, exist_ok=True)
            write_json(out / "plan.json", plan)
            (out / "task-cards.md").write_text(task_card_markdown(task, plan), encoding="utf-8")
            if args.json:
                print(json.dumps(plan,ensure_ascii=False,indent=2))
            else:
                print(json.dumps(plan["counts"], ensure_ascii=False))
                for message in plan["task_blockers"]: print("  -",message)
                for item in plan["instances"]:
                    print(item["instance_id"], item["requirement_status"], item["data_status"])
                    for message in item["blockers"]: print("  -", message)
                print(f"任务卡：{(out / 'task-cards.md').resolve()}")
            return 0 if not plan["task_blockers"] and all(not i["blockers"] for i in plan["instances"]) else 2
        elif args.command == "run":
            from .engine import execute
            run_dir, manifest = execute(args.task, args.out)
            print(f"成果目录：{run_dir}")
            print(f"状态：{manifest['status']}；导出文件 {manifest['generated_file_count']} 个。")
            for item in manifest["instances"]:
                print(item["instance_id"], item["run_status"], item.get("error", ""))
                for reason in item["blockers"]:
                    print("  -", reason)
            return 0 if manifest["status"] == "needs_review" else 2
        elif args.command=="audit":
            from .integrity import inspect_run
            result=inspect_run(args.run);print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0 if result["passed"] else 2
        elif args.command=="preview":
            from .delivery import preview
            print(preview(args.run));return 0
        elif args.command=="review":
            from .integrity import review_run
            result=review_run(args.run,args.reviewer,args.decision,args.note,args.instance)
            print(json.dumps({"status":result["status"],"review":result["reviews"][-1]},ensure_ascii=False,indent=2));return 0
        elif args.command=="repaint":
            from .repaint import repaint
            patch={k:getattr(args,k) for k in ("palette","page") if getattr(args,k) is not None}
            if not patch and args.title is None: raise ValueError("请提供样式修改或新标题。")
            run,result=repaint(args.run,patch,args.out,args.instance,args.title)
            print(json.dumps({"run":str(run),"status":result["status"],"analysis_reused":True},ensure_ascii=False));return 0
        elif args.command=="bundle":
            from .delivery import bundle
            result=bundle(args.run,args.out or args.run.resolve().parent/f"{args.run.name}-bundle.zip",not args.without_inputs)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        print(f"已保存：{args.task.resolve()}")
        return 0
    except (ValueError, OSError, TypeError,ImportError,RuntimeError) as exc:
        print(f"无法执行：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
