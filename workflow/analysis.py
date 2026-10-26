from collections import defaultdict

from shapely.geometry import mapping
from shapely.ops import unary_union
from shapely import STRtree

ALGORITHM_VERSION = "base-maps-0.2.0"
PATH_CLASSES = {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway"}


def classify(properties, parameters, did):
    if did == "fabric":
        return "建筑轮廓"
    raw = properties.get(parameters["classification_property"])
    if raw is None or raw == "":
        return "未知"
    if not isinstance(raw, (str, int, float, bool)):
        raise ValueError("分类属性需要标量值，不能用列表或对象作为图例。")
    raw = str(raw)
    rules = parameters.get("class_mapping", {})
    return (rules.get(raw) or "未知") if rules else raw


def analyze(did, parameters, dataset, ctx):
    groups, display_features = defaultdict(list), []
    used, excluded = [], {str(v) for v in parameters.get("exclude_ids", [])}
    all_ids = {f["id"] for f in dataset["features"]}
    if excluded - all_ids:
        raise ValueError(f"exclude_ids 含资料中不存在的 ID：{sorted(excluded-all_ids)}")
    features = [f for f in dataset["features"] if f["id"] not in excluded]
    if did == "gw" and parameters.get("overlap_policy") == "water_priority":
        water = unary_union([f["geometry"] for f in features
                             if classify(f["properties"],parameters,did) in parameters["water_classes"]])
        features = [{**f,"geometry":f["geometry"].difference(water)}
                    if classify(f["properties"],parameters,did) not in parameters["water_classes"] else f
                    for f in features]
        features = [f for f in features if not f["geometry"].is_empty]
    if did == "landuse" and parameters.get("overlap_policy") == "smallest_first":
        ordered = sorted(features, key=lambda f: (f["geometry"].area, f["id"]))
        geoms = [f["geometry"] for f in ordered]
        tree = STRtree(geoms)
        features = []
        for n, feature in enumerate(ordered):
            overlaps = [geoms[int(j)] for j in tree.query(feature["geometry"], predicate="intersects") if j < n]
            geom = feature["geometry"].difference(unary_union(overlaps)) if overlaps else feature["geometry"]
            if not geom.is_empty:
                features.append({**feature, "geometry": geom})
    for feature in features:
        props, geom = feature["properties"], feature["geometry"]
        if feature["id"] in excluded:
            continue
        if did == "roads" and not parameters["include_paths"] and str(
                props.get("highway") or props.get("class") or props.get(parameters["classification_property"])) in PATH_CLASSES:
            continue
        category = classify(props, parameters, did)
        # Preserve distinct bridge/tunnel/level costs; never build a planar routing graph.
        level = tuple(str(props.get(k, "")) for k in ("layer", "bridge", "tunnel")) if did == "roads" else ()
        clipped = geom.intersection(ctx.scopes["analysis"])
        measure = clipped.length if did == "roads" else clipped.area
        if not clipped.is_empty and measure > 0:
            groups[(category, level)].append(clipped)
            used.append(feature["id"])
        display = geom.intersection(ctx.scopes["display"])
        if not display.is_empty and (display.length if did == "roads" else display.area) > 0:
            display_features.append({"id": feature["id"], "category": category,
                                     "name": str(props.get("name") or ""),
                                     "geometry": mapping(display)})
    # Union within each class before metrics. Conflicting classes require a decision.
    unioned = {key: unary_union(geoms) for key, geoms in groups.items()}
    items = list(unioned.items())
    for n, ((category, level), geom) in enumerate(items):
        for (other_category, other_level), other in items[n+1:]:
            if category != other_category and level == other_level:
                intersection = geom.intersection(other)
                overlap = intersection.length if did == "roads" else intersection.area
                if overlap > 1e-6:
                    raise ValueError(f"分类 {category} / {other_category} 存在重叠（{overlap:.3f}）；"
                                     "请先核对分类冲突，避免重复统计。")
    metrics = defaultdict(float)
    for (category, _), geom in unioned.items():
        metrics[category] += geom.length if did == "roads" else geom.area
    total = sum(metrics.values())
    area = ctx.scopes["analysis"].area
    categories = sorted(set(metrics) | {f["category"] for f in display_features})
    if len(categories) > 16:
        raise ValueError("首版单图最多显示16类；请通过 class_mapping 合并分类。")
    table = [{"category": category, "value": metrics[category],
              "unit": "m" if did == "roads" else "m²",
              "share_percent": metrics[category] / total * 100 if total else 0,
              "analysis_area_percent": None if did == "roads" else metrics[category] / area * 100}
             for category in categories]
    return {
        "algorithm_version": ALGORITHM_VERSION, "diagram_id": did,
        "analysis_crs": ctx.crs.to_string(),
        "scopes": {k: mapping(v) for k, v in ctx.scopes.items()},
        "true_north_vector": ctx.true_north,
        "analysis_area_m2": area, "input_feature_count": len(dataset["features"]),
        "analysis_feature_count": len(used), "analysis_feature_ids": used,
        "display_feature_count": len(display_features),
        "total_length_m": total if did == "roads" else None,
        "total_area_m2": None if did == "roads" else total,
        "coverage_percent": None if did == "roads" else total / area * 100,
        "unmapped_area_m2": None if did == "roads" else max(0, area-total),
        "table": table, "display_features": display_features,
        "data_sha256": dataset["sha256"], "source": dataset["metadata"],
        "method": ("按分类与层级分组，线并集去重后统计长度，不生成通行网络。" if did == "roads" else
                   "按来源用途属性分类；叠置处小面优先，在分析范围内统计分区面积。" if did == "landuse" and parameters.get("overlap_policy") == "smallest_first" else
                   "水域优先，绿地扣除重叠水面；按研究范围内并集面积统计。" if did == "gw" and parameters.get("overlap_policy") == "water_priority" else
                   "按分类合并重叠面，在分析范围内计算并集面积；分母为分析范围面积。"),
        "editable_parts": "SVG线、面、文字可编辑；依赖本地可用字体。PDF为矢量，PNG为栅格。",
    }
