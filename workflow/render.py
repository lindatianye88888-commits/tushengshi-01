"""Shared, source-aware map rendering for single sheets and contact boards."""
import math
import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt, font_manager
from matplotlib.collections import PatchCollection, LineCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path as MplPath
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from pyproj import Transformer
from .catalog import DIAGRAMS
from .styles import PALETTES, ROAD_WIDTHS, TITLES, diagram_colors, effective_style, validate_style

RENDER_VERSION = "site-atlas-0.4.0"


def font_setup():
    available = {font.name for font in font_manager.fontManager.ttflist}
    for family in ("Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC", "SimHei", "PingFang SC"):
        if family in available:
            return family
    raise ValueError("中文出图需要微软雅黑、思源黑体或 Noto Sans CJK SC。")


def polygon_patches(geom, color, width=.12, fill=True, linestyle="-"):
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        polygon = orient(geom, sign=1)
        vertices, codes = [], []
        for ring in [polygon.exterior, *polygon.interiors]:
            points = list(ring.coords)
            vertices.extend(points)
            codes.extend([MplPath.MOVETO] + [MplPath.LINETO]*(len(points)-2) + [MplPath.CLOSEPOLY])
        return [PathPatch(MplPath(vertices, codes), facecolor=color if fill else "none", edgecolor=color, linewidth=width, linestyle=linestyle)]
    if hasattr(geom, "geoms"):
        return [patch for child in geom.geoms for patch in polygon_patches(child, color, width, fill, linestyle)]
    return []


def draw_geometry(ax, geom, color, width=.6, fill=True, linestyle="-"):
    if geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        for patch in polygon_patches(geom, color, width, fill, linestyle):
            ax.add_patch(patch)
    elif geom.geom_type in {"LineString", "LinearRing"}:
        ax.plot(*geom.xy, color=color, linewidth=width, solid_capstyle="round", linestyle=linestyle)
    elif hasattr(geom, "geoms"):
        for child in geom.geoms:
            draw_geometry(ax, child, color, width, fill, linestyle)


def wrapped(text, width=58):
    lines, current, used = [], "", 0
    for char in str(text):
        cost = 2 if ord(char) > 127 else 1
        if used+cost > width:
            lines.append(current)
            current, used = "", 0
        current += char
        used += cost
    return "\n".join([*lines, current])


def draw_map(ax, result, project, parameters, colors, compact=False):
    display = shape(result["scopes"]["display"])
    analysis = shape(result["scopes"]["analysis"])
    draw_geometry(ax, display, "#eeeee6", width=0)
    if result["diagram_id"] == "roads":
        lines, line_colors, widths = [], [], []
        for feature in result["display_features"]:
            geom = shape(feature["geometry"])
            parts = [geom] if geom.geom_type == "LineString" else list(geom.geoms) if hasattr(geom, "geoms") else []
            for part in parts:
                if part.geom_type == "LineString":
                    lines.append(list(part.coords))
                    line_colors.append(colors[feature["category"]])
                    widths.append(ROAD_WIDTHS.get(feature["category"], .5)*(.65 if compact else 1))
        if lines:
            ax.add_collection(LineCollection(lines, colors=line_colors, linewidths=widths))
    else:
        patches = [patch for feature in result["display_features"] for patch in polygon_patches(shape(feature["geometry"]), colors[feature["category"]])]
        if patches:
            ax.add_collection(PatchCollection(patches, match_original=True))
    draw_geometry(ax, display, "#34584b", width=.6 if compact else 1.15, fill=False)
    if not display.equals(analysis):
        draw_geometry(ax, analysis.intersection(display), "#ad795e", width=.9, fill=False, linestyle="--")
    x0, y0, x1, y1 = display.bounds
    margin = max(x1-x0, y1-y0)*.04
    ax.set_xlim(x0-margin, x1+margin)
    ax.set_ylim(y0-margin, y1+margin)
    ax.set_aspect("equal")
    ax.set_axis_off()
    location = project.get("location")
    if location and "lon" in location and "lat" in location:
        cx, cy = Transformer.from_crs(4326, result["analysis_crs"], always_xy=True).transform(location["lon"], location["lat"])
        ax.plot(cx, cy, marker="o", ms=4 if compact else 5.5, mfc="#f6f4ec", mec="#a34e43", mew=1.2, zorder=20)
        ax.annotate(location.get("label") or "项目中心", (cx, cy), xytext=(7, 7), textcoords="offset points", size=6.5 if compact else 8, color="#8f493f", zorder=21, bbox={"facecolor":"#f5f4ee", "edgecolor":"none", "alpha":.9})
    if result["diagram_id"] == "roads" and parameters.get("show_names"):
        placed, names = [], set()
        for feature in result["display_features"]:
            if not feature["name"] or feature["name"] in names:
                continue
            point = shape(feature["geometry"]).representative_point()
            if all(point.distance(other) > max(x1-x0, y1-y0)*.11 for other in placed):
                ax.text(point.x, point.y, feature["name"], size=6.5, clip_on=True, bbox={"facecolor":"#f5f4ee", "edgecolor":"none", "alpha":.85})
                placed.append(point)
                names.add(feature["name"])
    north = result["true_north_vector"]
    start = (x0+.9*(x1-x0), y0+.78*(y1-y0))
    length = min(x1-x0, y1-y0)*.12
    ax.annotate("N", xy=(start[0]+north[0]*length, start[1]+north[1]*length), xytext=start, ha="center", size=7 if compact else 10, color="#26352e", arrowprops={"arrowstyle":"-|>", "color":"#26352e", "lw":1})
    target = (x1-x0)*.2
    magnitude = 10**math.floor(math.log10(target))
    scale = next((n*magnitude for n in (5, 2, 1) if n*magnitude <= target), magnitude/2)
    bx, by = x0+.05*(x1-x0), y0+.06*(y1-y0)
    ax.plot([bx, bx+scale], [by, by], color="#26352e", lw=1.5)
    ax.text(bx, by+margin*.25, f"{scale:g} m", size=6 if compact else 8, va="bottom", color="#26352e")


def credits(result):
    source = result["source"]
    return source.get("attributions", [source.get("source", "")])


def draw_result(ax, result, project, parameters, colors, compact=False):
    if result.get("cartographic_kind"):
        from .cartography_render import draw_panel
        draw_panel(ax,result,project,parameters,colors,compact)
    elif result.get("display_kind"):
        from .statistics_render import draw_panel
        draw_panel(ax, result, project, parameters, colors, compact)
    else:
        draw_map(ax, result, project, parameters, colors, compact)


def draw_standard_summary(side, result, item, colors):
    ink, accent = "#26352e", "#34584b"
    side.set_axis_off()
    side.text(0, 1, "统计与图例", size=13, weight="bold", color=ink, va="top")
    is_roads = item["diagram_id"] == "roads"
    metric = f"{result['total_length_m']/1000:,.1f} km" if is_roads else f"{result['coverage_percent']:.2f}%"
    side.text(0, .89, metric, size=24, color=accent)
    side.text(0, .835, "已收录道路线总长度" if is_roads else "已收录轮廓面积 / 分析范围面积", size=9, color="#667168")
    side.text(0, .785, f"统计范围 {result['analysis_area_m2']:,.0f} m²\n参与统计对象 {result['analysis_feature_count']} 个", size=8.5, linespacing=1.7, color=ink)
    rows = list(result["table"])
    if item["diagram_id"] == "landuse":
        rows.append({"category":"未收录 / 未分类", "value":result.get("unmapped_area_m2", 0), "unit":"m²"})
        colors["未收录 / 未分类"] = "#eeeee6"
    step = min(.057, .57/max(len(rows), 1))
    for n,row in enumerate(rows):
        y = .665-n*step
        side.plot([0, .05], [y, y], color=colors[row["category"]], lw=4)
        label = row["category"] if len(row["category"]) <= 16 else row["category"][:15]+"…"
        side.text(.085, y, label, size=8, va="center", color=ink)
        number = f"{row['value']/1000:,.2f} km" if is_roads else f"{row['value']/1e6:,.3f} km²"
        side.text(1, y-.022, number, size=7.3, va="center", ha="right", color="#667168")
    side.set_xlim(0, 1);side.set_ylim(0, 1)


def export(result, task, item, output_dir):
    style = validate_style(effective_style(task, item))
    colors = diagram_colors(result, style)
    inches = (16.5354, 11.6929) if style["page"] == "A3" else (11.6929, 8.2677)
    rc = {"font.family":font_setup(), "svg.fonttype":"none", "pdf.fonttype":42, "axes.unicode_minus":False, "svg.hashsalt":RENDER_VERSION}
    with plt.rc_context(rc):
        fig = plt.figure(figsize=inches, facecolor="#f5f4ee")
        try:
            ink, accent = "#26352e", "#34584b"
            fig.text(.045, .944, "场地分析  /  SITE ATLAS", size=10, color=accent, weight="bold")
            fig.text(.045, .895, f"{TITLES.get(item['diagram_id'], item['name'])}  /  {DIAGRAMS[item['diagram_id']]['app_name'].upper()}", size=23, color=ink)
            fig.text(.045, .857, wrapped(task["project"].get("name", "未命名项目"), 80), size=10, color="#667168")
            if task.get("is_example"):
                fig.text(.955, .944, "合成数据 · 流程验证", ha="right", size=11, color="#a15d3c")
            ax = fig.add_axes([.045, .19, .66, .62])
            draw_result(ax, result, task["project"], {k:v["value"] for k,v in item["parameters"].items()}, colors)
            side = fig.add_axes([.745, .19, .21, .62])
            if result.get("cartographic_kind"):
                from .cartography_render import draw_summary
                draw_summary(side,result,colors)
            elif result.get("display_kind"):
                from .statistics_render import draw_summary
                draw_summary(side, result, colors)
            else:
                draw_standard_summary(side, result, item, colors)
            source = result["source"]
            boundary = "资料完整性按来源声明，指标仅对应已提供资料。" if source.get("mapping_completeness") == "verified_complete" else "现实完整性未核实；地图空白不等于现实空地。"
            notes = [f"坐标 {result['analysis_crs']} | 实线：显示范围；虚线（不同时）：统计范围 | 真北 N", f"来源：{source['source']} | 时段：{source['data_period']} | 获取：{source['acquired_at']}", f"方法：{result['method']}", f"数据 {result['data_sha256'][:12]} | 算法 {result['algorithm_version']} | {boundary}"]
            if result.get("display_kind") in {"orientation_rose", "typology_catalog"}:
                notes[0] = f"原始分析坐标 {result['analysis_crs']} | "+("方位以场地中心真北为0°；扇区数据见CSV。" if result["display_kind"]=="orientation_rose" else "所有轮廓卡片保持同一米制比例；完整来源ID与原坐标保留在明细及分析记录。")
            if result.get('cartographic_kind'):
                notes[0]=f"分析投影 {result['analysis_crs']} | "+('各行政视窗独立采用投影近似比例；标记为同一项目点。' if result['cartographic_kind'] in {'context','location'} else '方法及支持模式见任务卡；不由图形推断缺失的现实属性。')
            for n,note in enumerate(notes):
                fig.text(.045, .139-n*.023, wrapped(note, 160), size=7, color="#667168", va="top")
            attribution = "; ".join(credits(result))
            if source.get("geometry_note"):
                attribution = source["geometry_note"]+" "+attribution
            fig.text(.045, .032, wrapped(attribution, 180), size=7, color="#667168", va="top")
            outputs = []
            for fmt in item["output_formats"]:
                path = output_dir/f"{item['instance_id']}.{fmt}"
                fig.savefig(path, format=fmt, dpi=160, facecolor=fig.get_facecolor())
                outputs.append(path)
            return outputs
        finally:
            plt.close(fig)


def contact_board(task, items, results, path):
    if not items:
        raise ValueError("没有可预览成果。")
    columns = 2 if len(items)==4 else min(3, len(items))
    rows = math.ceil(len(items)/columns)
    with plt.rc_context({"font.family":font_setup(), "axes.unicode_minus":False}):
        fig = plt.figure(figsize=(6*columns, 6.4*rows+1.5), facecolor="#f5f4ee")
        try:
            fig.text(.035, .96, task["project"].get("name", "项目成果"), size=21, color="#34584b", weight="bold")
            fig.text(.035, .925, f"{len(items)} 张图纸 · 分类图例、统计口径与来源见单图和运行记录", size=10, color="#667168")
            for n,item in enumerate(items):
                row,col = divmod(n, columns)
                left = .025+col*.95/columns
                cell_height=.81/rows
                bottom = .09+(rows-row-1)*cell_height
                ax = fig.add_axes([left, bottom+.075, .93/columns, cell_height-.09])
                result = results[item["instance_id"]]
                draw_result(ax, result, task["project"], {k:v["value"] for k,v in item["parameters"].items()}, diagram_colors(result, effective_style(task, item)), compact=True)
                fig.text(left+.465/columns, bottom+.03, TITLES.get(item["diagram_id"], item["name"]), ha="center", size=13, color="#34584b", weight="bold")
                if result.get("cartographic_kind"):
                    from .cartography_render import summary_text
                    note=summary_text(result)
                elif result.get("display_kind"):
                    from .statistics_render import summary_text
                    note = summary_text(result)
                else:
                    note = f"道路线 {result['total_length_m']/1000:.1f} km" if item["diagram_id"] == "roads" else f"已收录面积占比 {result['coverage_percent']:.1f}%"
                fig.text(left+.465/columns, bottom+.005, note, ha="center", size=9, color="#667168")
            all_credits = list(dict.fromkeys(c for result in results.values() for c in credits(result)))
            fig.text(.035, .055, wrapped("来源署名："+"; ".join(all_credits), 200), size=7, color="#667168", va="top")
            if task.get("is_example"):
                fig.text(.965, .96, "合成数据 · 流程验证", ha="right", size=10, color="#a15d3c")
            fig.savefig(path, dpi=160, facecolor=fig.get_facecolor())
            return path
        finally:
            plt.close(fig)
