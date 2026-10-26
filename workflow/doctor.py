import importlib
import platform
import sys


def diagnose():
    packages={}
    for name in ("shapely","pyproj","matplotlib","requests","pyarrow"):
        try:
            module=importlib.import_module(name)
            packages[name]={"available":True,"version":getattr(module,"__version__","unknown")}
        except ImportError:
            packages[name]={"available":False}
    font=None
    if packages["matplotlib"]["available"]:
        try:
            from .render import font_setup
            font=font_setup()
        except ValueError: pass
    return {"python":sys.version.split()[0],"platform":platform.system(),"python_executable":sys.executable,
            "packages":packages,"chinese_font":font,
            "can_render":all(packages[name]["available"] for name in ("shapely","pyproj","matplotlib")) and bool(font),
            "can_fetch":all(packages[name]["available"] for name in ("shapely","pyproj","requests","pyarrow")),
            "desktop_requirement":"桌面端须具备本地读写与命令工具；本诊断不检测或宣称任何客户端已授予这些权限。"}
