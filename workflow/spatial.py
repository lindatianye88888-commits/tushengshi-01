"""Explicit CRS, scopes and provenance checks for line and polygon datasets."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform, unary_union
from shapely.validation import explain_validity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def geometry(value, label, polygon=False):
    try:
        geom = shape(value)
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise ValueError(f"{label} 不是有效 GeoJSON 几何：{exc}") from exc
    if geom.is_empty or not geom.is_valid or not all(math.isfinite(n) for n in geom.bounds):
        raise ValueError(f"{label} 为空或无效：{explain_validity(geom)}")
    if polygon and geom.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"{label} 必须是 Polygon / MultiPolygon。")
    return geom


def project_geom(geom, source_crs, target_crs):
    fn = Transformer.from_crs(source_crs, target_crs, always_xy=True).transform
    result = transform(fn, geom)
    if not result.is_valid or result.is_empty or not all(math.isfinite(n) for n in result.bounds):
        raise ValueError("坐标转换后几何无效，请核对坐标与覆盖范围。")
    return result


@dataclass
class SpatialContext:
    crs: CRS
    source_crs: CRS
    scopes: dict
    true_north: tuple


def context(project):
    if not project.get("source_crs") or not project.get("analysis_crs"):
        raise ValueError("请填写 project.source_crs 与 analysis_crs。")
    source = CRS.from_user_input(project["source_crs"])
    crs = CRS.from_user_input(project["analysis_crs"])
    if not crs.is_projected or len(crs.axis_info) < 2 or any(
            abs(a.unit_conversion_factor - 1) > 1e-9 for a in crs.axis_info[:2]):
        raise ValueError("analysis_crs 必须是以米为单位的投影坐标系，不能直接使用经纬度或英尺。")
    # Web Mercator is unsuitable for this first adapter's local area/length metrics.
    if crs.to_epsg() == 3857:
        raise ValueError("面积/长度统计请选场地适用的本地米制投影，不能使用 EPSG:3857。")
    if project.get("north_rotation_degrees", 0) != 0:
        raise ValueError("首版仅支持投影网格北朝上；请将 north_rotation_degrees 设为 0。")
    boundary = geometry(project.get("boundary"), "project.boundary", polygon=True)
    overrides = project.get("scopes", {})
    scopes = {key: project_geom(geometry(overrides.get(key) or mapping(boundary),
                                        f"scopes.{key}", polygon=True), source, crs)
              for key in ("display", "analysis", "acquisition")}
    needed = unary_union([scopes["display"], scopes["analysis"]])
    if not scopes["acquisition"].buffer(1e-6).covers(needed):
        raise ValueError("取数范围 acquisition 必须覆盖显示范围与统计范围。")
    x, y = scopes["display"].centroid.coords[0]
    to_geo = Transformer.from_crs(crs, 4326, always_xy=True)
    lon, lat = to_geo.transform(x, y)
    if not (-80 < lat < 84):
        raise ValueError("首版仅校核南纬80°至北纬84°的局地场地。")
    to_map = Transformer.from_crs(4326, crs, always_xy=True)
    nx, ny = to_map.transform(lon, lat + 0.001)
    length = math.hypot(nx-x, ny-y)
    return SpatialContext(crs, source, scopes, ((nx-x)/length, (ny-y)/length))


def load_dataset(resource_id, spec, base_dir, ctx):
    if not isinstance(spec, dict):
        raise ValueError(f"缺少资料 {resource_id}。")
    mandatory = ("path", "source", "acquired_at", "data_period", "crs", "units",
                 "coverage", "completeness", "estimated_fields")
    missing = [k for k in mandatory if k not in spec or spec[k] is None or spec[k] == ""]
    if missing:
        raise ValueError(f"{resource_id} 缺少资料元数据：{', '.join(missing)}")
    if spec["completeness"] not in {"complete", "source_complete"}:
        raise ValueError(f"{resource_id} 取数完整性未确认，需要 complete 或 source_complete。")
    if not isinstance(spec["estimated_fields"], list):
        raise ValueError(f"{resource_id}.estimated_fields 必须是列表。")
    crs = CRS.from_user_input(spec["crs"])
    coverage = project_geom(geometry(spec["coverage"], f"{resource_id}.coverage", True), crs, ctx.crs)
    if not coverage.buffer(1e-6).covers(ctx.scopes["acquisition"]):
        raise ValueError(f"{resource_id} 的声明覆盖范围不足以覆盖取数范围。")
    path = (Path(base_dir) / spec["path"]).resolve()
    raw = path.read_bytes()
    if spec.get("sha256") and spec["sha256"]!=hashlib.sha256(raw).hexdigest():
        raise ValueError(f"{resource_id} 文件与登记的数据哈希不一致。")
    data = json.loads(raw.decode("utf-8-sig"))
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise ValueError(f"{resource_id} 需要 GeoJSON FeatureCollection。")
    if not data["features"] and not spec.get("verified_empty", False):
        raise ValueError(f"{resource_id} 空资料不能默认当作零；核实后设置 verified_empty=true。")
    if "crs" in data:
        embedded = data["crs"].get("properties", {}).get("name")
        if not embedded or not CRS.from_user_input(embedded).equals(crs):
            raise ValueError(f"{resource_id} 文件 CRS 声明与资料元数据不一致。")
    features, ids = [], set()
    allowed = ({"LineString", "MultiLineString"} if resource_id == "roads" else
               {"Point"} if resource_id == "connectors" else {"Polygon", "MultiPolygon"})
    for feature in data["features"]:
        fid = feature.get("id")
        if not isinstance(fid, (str, int)) or str(fid) in ids:
            raise ValueError(f"{resource_id} 的每个对象需要唯一字符串/整数 id。")
        ids.add(str(fid))
        geom = geometry(feature.get("geometry"), f"{resource_id}/{fid}")
        if geom.geom_type not in allowed:
            raise ValueError(f"{resource_id}/{fid} 几何类型不支持：{geom.geom_type}")
        if geom.has_z:
            raise ValueError(f"{resource_id}/{fid} 首版只接受二维分析几何。")
        try:
            if not crs.equals(ctx.crs):
                geom = project_geom(geom, crs, ctx.crs)
        except ValueError as exc:
            raise ValueError(f"{resource_id}/{fid}: {exc}") from exc
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            raise ValueError(f"{resource_id}/{fid} properties 必须是对象。")
        features.append({"id": str(fid), "geometry": geom, "properties": props})
    return {"resource_id": resource_id, "features": features, "source_path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(), "metadata": spec,"raw_bytes":raw}
