"""Project creation and scope changes, independent of any particular city."""
import copy
import math

from pyproj import Transformer
from shapely.geometry import Point, mapping

from .task import event


def circle_project(project, lon, lat, radius_m, padding_m=None, label=None):
    values = (lon, lat, radius_m)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values):
        raise ValueError("经纬度与半径需要有限数值。")
    if not -180 <= lon <= 180 or not -80 < lat < 84:
        raise ValueError("坐标需为 WGS84 经度[-180,180]、纬度(-80,84)。")
    if not 0 < radius_m <= 50000:
        raise ValueError("当前局地出图支持大于0、至多50,000米的半径。")
    padding_m = max(200, radius_m * .1) if padding_m is None else padding_m
    if not isinstance(padding_m, (int, float)) or not math.isfinite(padding_m) or padding_m < 0:
        raise ValueError("取数外扩 padding_m 需要非负有限数值。")
    zone = min(60, math.floor((lon + 180) / 6) + 1)
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    if 72 <= lat < 84 and 0 <= lon < 42:
        zone = 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    epsg = (32600 if lat >= 0 else 32700) + zone
    crs = f"EPSG:{epsg}"
    x, y = Transformer.from_crs(4326, crs, always_xy=True).transform(lon, lat)
    center = Point(x, y)
    display = mapping(center.buffer(radius_m, quad_segs=256))
    return {**copy.deepcopy(project), "source_crs": crs, "analysis_crs": crs,
            "location": {"lon": lon, "lat": lat, "crs": "EPSG:4326",
                         "label": label or project.get("location", {}).get("label") or project.get("name", "项目中心"),
                         "source": "user_or_agent_verified_wgs84"},
            "boundary": display, "north_rotation_degrees": 0,
            "scopes": {"display": display, "analysis": copy.deepcopy(display),
                       "acquisition": mapping(center.buffer(radius_m+padding_m, quad_segs=256))},
            "radius_m": radius_m, "acquisition_padding_m": padding_m,
            "radius_definition": "场地所在UTM投影平面半径，非路网通行距离"}


def set_scope(task, **settings):
    previous = {key: task["project"].get(key) for key in ("location", "radius_m", "analysis_crs")}
    task["project"] = circle_project(task["project"], **settings)
    event(task, "scope_changed", before=previous,
          after={key: task["project"].get(key) for key in previous})


def spatial_signature(project):
    return {key: project.get(key) for key in
            ("source_crs", "analysis_crs", "boundary", "scopes", "north_rotation_degrees")}
