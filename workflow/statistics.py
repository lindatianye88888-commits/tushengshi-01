"""Spatial statistics with explicit units, scopes and supported modes."""
import math

from shapely import STRtree, normalize
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .catalog import DEFAULTS

STATISTICS_VERSION = "spatial-statistics-0.3.0"
STATISTICAL_DIAGRAMS = {"density", "orientrose", "typology"}
PATH_CLASSES = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway"}
DENSITY_CLASSES = ("0%", "0–10%", "10–20%", "20–40%", "40–60%", "60–100%")


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_parameters(did, parameters, ctx=None):
    if did == "density":
        if parameters.get("mode") != "coverage":
            raise ValueError("density 当前仅支持 mode=coverage（轮廓覆盖率），尚不支持容积率。")
        size = parameters.get("grid_size_m")
        if not finite_number(size) or not 25 <= size <= 5000:
            raise ValueError("grid_size_m 需要25至5000之间的米数。")
        if ctx:
            x0, y0, x1, y1 = ctx.scopes["analysis"].bounds
            count = (math.ceil(x1/size)-math.floor(x0/size))*(math.ceil(y1/size)-math.floor(y0/size))
            if count > 10000:
                raise ValueError("网格超过10000格，请增大 grid_size_m 或缩小统计范围。")
    elif did == "orientrose":
        count = parameters.get("bin_count")
        if not isinstance(count, int) or isinstance(count, bool) or not 4 <= count <= 36:
            raise ValueError("bin_count 需要4至36之间的整数。")
        if parameters.get("weighting") != "length":
            raise ValueError("orientrose 当前仅支持 weighting=length。")
        if not isinstance(parameters.get("fold_opposite"), bool) or not isinstance(parameters.get("include_paths"), bool):
            raise ValueError("fold_opposite 和 include_paths 需要 true / false。")
    elif did == "typology":
        count = parameters.get("max_buildings")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 72:
            raise ValueError("max_buildings 需要1至72之间的整数。")
        area = parameters.get("min_area_m2")
        if not finite_number(area) or area < 0:
            raise ValueError("min_area_m2 需要非负有限面积。")
        if parameters.get("sort_by") != "area_desc" or parameters.get("same_scale") is not True:
            raise ValueError("typology 当前支持 area_desc 排序与 same_scale=true。")


def selected_features(dataset, parameters):
    excluded = {str(value) for value in parameters.get("exclude_ids", [])}
    unknown = excluded - {f["id"] for f in dataset["features"]}
    if unknown:
        raise ValueError(f"exclude_ids 含资料中不存在的 ID：{sorted(unknown)}")
    return [f for f in dataset["features"] if f["id"] not in excluded]


def parts(geometry, kind):
    if geometry.is_empty:
        return
    if geometry.geom_type == kind:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for child in geometry.geoms:
            yield from parts(child, kind)


def density(features, parameters, ctx):
    boundary = ctx.scopes["analysis"]
    used = [f for f in features if f["geometry"].intersection(boundary).area > 0]
    covered = unary_union([f["geometry"].intersection(boundary) for f in used])
    polygons = list(parts(covered, "Polygon"))
    tree = STRtree(polygons)
    size = parameters["grid_size_m"]
    x0, y0, x1, y1 = boundary.bounds
    values = dict.fromkeys(DENSITY_CLASSES, 0.0)
    cells, display = [], []
    for ix in range(math.floor(x0/size), math.ceil(x1/size)):
        for iy in range(math.floor(y0/size), math.ceil(y1/size)):
            cell = box(ix*size, iy*size, (ix+1)*size, (iy+1)*size).intersection(boundary)
            if cell.area <= 1e-9:
                continue
            area = sum(polygons[int(j)].intersection(cell).area for j in tree.query(cell, predicate="intersects"))
            ratio = min(100.0, max(0.0, area/cell.area*100))
            index = 0 if area <= 1e-9 else next((n for n, limit in enumerate((10, 20, 40, 60, 100), 1) if ratio <= limit), 5)
            category = DENSITY_CLASSES[index]
            values[category] += area
            record = {"cell_id": f"{ix}:{iy}", "effective_area_m2": cell.area,
                      "building_area_m2": area, "coverage_percent": ratio, "density_class": category}
            cells.append(record)
            visible = cell.intersection(ctx.scopes["display"])
            if visible.area > 0:
                display.append({"id": record["cell_id"], "category": category, "geometry": mapping(visible)})
    total = covered.area
    table = [{"category": category, "value": value, "unit": "m²",
              "share_percent": value/total*100 if total else 0,
              "analysis_area_percent": value/boundary.area*100} for category, value in values.items()]
    return {"display_kind": "density_grid", "analysis_feature_ids": [f["id"] for f in used],
            "total_area_m2": total, "total_length_m": None,
            "coverage_percent": total/boundary.area*100, "unmapped_area_m2": None,
            "grid_size_m": size, "cell_count": len(cells), "detail_table": cells,
            "display_features": display, "table": table,
            "method": "建筑轮廓裁剪后取并集；网格分母为与统计范围相交的有效面积；整体为轮廓覆盖率，不是容积率。"}


def orientrose(features, parameters, ctx):
    grouped, used = {}, []
    for feature in features:
        props = feature["properties"]
        if not parameters["include_paths"] and str(props.get("highway") or props.get("class")) in PATH_CLASSES:
            continue
        clipped = feature["geometry"].intersection(ctx.scopes["analysis"])
        lines = list(parts(clipped, "LineString"))
        if not lines:
            continue
        used.append(feature["id"])
        level = tuple(str(props.get(key, "")) for key in ("layer", "bridge", "tunnel"))
        grouped.setdefault(level, []).extend(lines)
    count = parameters["bin_count"]
    period = 180 if parameters["fold_opposite"] else 360
    width = period/count
    weights = [0.0]*count
    true_north = math.atan2(*ctx.true_north)
    for lines in grouped.values():
        for line in parts(unary_union(lines), "LineString"):
            coords = list(line.coords)
            for a, b in zip(coords, coords[1:]):
                dx, dy = b[0]-a[0], b[1]-a[1]
                length = math.hypot(dx, dy)
                if length <= 0:
                    continue
                bearing = math.degrees(math.atan2(dx, dy)-true_north) % period
                bearings = [(bearing, length)] if period == 180 else [(bearing, length/2), ((bearing+180)%360, length/2)]
                for angle, weight in bearings:
                    weights[min(int(angle/width), count-1)] += weight
    total = sum(weights)
    table = [{"category": f"{n*width:g}–{(n+1)*width:g}°", "value": value, "unit": "m",
              "share_percent": value/total*100 if total else 0, "analysis_area_percent": None}
             for n, value in enumerate(weights)]
    return {"display_kind": "orientation_rose", "analysis_feature_ids": used,
            "total_area_m2": None, "total_length_m": total, "coverage_percent": None,
            "unmapped_area_m2": None, "bin_width_degrees": width, "period_degrees": period,
            "dominant_orientation_degrees": (max(range(count), key=weights.__getitem__)+.5)*width if total else None,
            "display_features": [], "table": table,
            "detail_table": [{"start_degrees": n*width, "end_degrees": (n+1)*width,
                              "length_m": value, "share_percent": value/total*100 if total else 0} for n, value in enumerate(weights)],
            "method": ("按层级合并重复线，逐折点拆段并按长度加权，方位参照场地中心真北；"+
                       ("相反方向合并，图面平分到对向扇区。" if period == 180 else "无向中心线长度平分到两个相反方向。"))}


def typology(features, parameters, ctx):
    candidates, seen = [], set()
    for feature in sorted(features, key=lambda f: f["id"]):
        geom = feature["geometry"]
        if geom.area <= 0 or geom.area < parameters["min_area_m2"] or not ctx.scopes["analysis"].covers(geom):
            continue
        signature = normalize(geom).wkb
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(feature)
    candidates.sort(key=lambda f: (-f["geometry"].area, f["id"]))
    chosen = candidates[:parameters["max_buildings"]]
    total = sum(f["geometry"].area for f in chosen)
    details = [{"rank": n+1, "source_id": f["id"], "area_m2": f["geometry"].area,
                "perimeter_m": f["geometry"].length,
                "compactness": 4*math.pi*f["geometry"].area/f["geometry"].length**2} for n, f in enumerate(chosen)]
    return {"display_kind": "typology_catalog", "analysis_feature_ids": [f["id"] for f in chosen],
            "eligible_feature_count": len(candidates), "total_area_m2": total, "total_length_m": None,
            "coverage_percent": None, "unmapped_area_m2": None, "same_scale": True,
            "display_features": [{"id": f["id"], "category": f["id"], "geometry": mapping(f["geometry"])} for f in chosen],
            "table": [{"category": f["id"], "value": f["geometry"].area, "unit": "m²",
                       "share_percent": f["geometry"].area/total*100 if total else 0,
                       "analysis_area_percent": None} for f in chosen], "detail_table": details,
            "method": "只选完整位于统计范围的建筑，按最小面积筛选、相同轮廓去重、面积降序选取；保持同一比例，不推断用途、年代或结构。"}


def analyze_statistics(did, parameters, dataset, ctx):
    parameters = {**DEFAULTS[did], **parameters}
    validate_parameters(did, parameters, ctx)
    result = {"algorithm_version": STATISTICS_VERSION, "diagram_id": did,
              "analysis_crs": ctx.crs.to_string(), "scopes": {key: mapping(value) for key, value in ctx.scopes.items()},
              "true_north_vector": ctx.true_north, "analysis_area_m2": ctx.scopes["analysis"].area,
              "input_feature_count": len(dataset["features"]), "data_sha256": dataset["sha256"],
              "source": dataset["metadata"], "editable_parts": "SVG图形和文字可编辑；统计明细另存CSV。"}
    result.update({"density": density, "orientrose": orientrose, "typology": typology}[did](selected_features(dataset, parameters), parameters, ctx))
    result["analysis_feature_count"] = len(result["analysis_feature_ids"])
    result["display_feature_count"] = len(result["display_features"])
    return result
