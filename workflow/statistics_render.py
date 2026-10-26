"""Dedicated map, polar-chart and same-scale footprint-catalog panels."""
import math

from matplotlib.ticker import PercentFormatter
from shapely.affinity import translate
from shapely.geometry import shape

from .render import draw_map, polygon_patches


def summary_text(result):
    kind = result["display_kind"]
    if kind == "density_grid":
        return f"轮廓覆盖率 {result['coverage_percent']:.2f}% · {result['grid_size_m']:g} m 网格"
    if kind == "orientation_rose":
        return f"长度加权 · 道路线 {result['total_length_m']/1000:.2f} km"
    return f"统一比例 · {result['analysis_feature_count']} 个完整建筑轮廓"


def draw_panel(ax, result, project, parameters, colors, compact=False):
    kind = result["display_kind"]
    if kind == "density_grid":
        draw_map(ax, result, project, parameters, colors, compact)
    elif kind == "orientation_rose":
        draw_rose(ax, result, colors, compact)
    else:
        draw_typology(ax, result, colors, compact)


def draw_rose(ax, result, colors, compact=False):
    ax.set_axis_off()
    polar = ax.inset_axes([.1, .04, .8, .9], projection="polar")
    polar.set_facecolor("#f5f4ee")
    polar.set_theta_zero_location("N")
    polar.set_theta_direction(-1)
    polar.set_thetagrids(range(0, 360, 45), labels=["N", "NE", "E", "SE", "S", "SW", "W", "NW"])
    width = result["bin_width_degrees"]
    total = result["total_length_m"]
    centers = [(n+.5)*width for n in range(len(result["table"]))]
    values = [row["value"]/total*100 if total else 0 for row in result["table"]]
    if result["period_degrees"] == 180:
        centers = centers+[center+180 for center in centers]
        values = [value/2 for value in values]*2
    polar.bar([math.radians(value) for value in centers], values,
              width=math.radians(width)*.92, color=next(iter(colors.values())), edgecolor="#f5f4ee", linewidth=.5)
    polar.yaxis.set_major_formatter(PercentFormatter(xmax=100, decimals=0))
    polar.set_ylim(0, max(values, default=0)*1.2 if any(values) else 1)
    polar.set_rlabel_position(20)
    polar.tick_params(labelsize=7 if compact else 9, colors="#526458")
    polar.grid(alpha=.25)
    polar.spines["polar"].set_color("#a4b3a1")
    if not total:
        polar.text(.5, .5, "来源内无可统计线段", transform=polar.transAxes, ha="center", fontsize=9)


def draw_typology(ax, result, colors, compact=False):
    ax.set_axis_off()
    features = result["display_features"]
    if not features:
        ax.text(.5, .5, "来源内无符合筛选条件的完整建筑轮廓", ha="center", transform=ax.transAxes)
        return
    count = len(features)
    columns = min(6, math.ceil(math.sqrt(count*1.5)))
    rows = math.ceil(count/columns)
    geoms = [shape(feature["geometry"]) for feature in features]
    span = max(max(g.bounds[2]-g.bounds[0], g.bounds[3]-g.bounds[1]) for g in geoms)*1.12
    for n, (feature, geom) in enumerate(zip(features, geoms)):
        row, col = divmod(n, columns)
        cell = ax.inset_axes([col/columns+.012, .1+(1-(row+1)/rows)*.9+.05/rows, .97/columns, .78/rows*.9])
        x0, y0, x1, y1 = geom.bounds
        centered = translate(geom, -(x0+x1)/2, -(y0+y1)/2)
        for patch in polygon_patches(centered, colors[feature["category"]], .25):
            cell.add_patch(patch)
        cell.set_xlim(-span/2, span/2);cell.set_ylim(-span/2, span/2)
        cell.set_aspect("equal");cell.set_axis_off()
        label = f"{n+1:02d} · {geom.area:,.0f} m²"
        cell.text(.5, -.05, label, ha="center", va="top", size=4.6 if compact else 6.3, transform=cell.transAxes)
    ax.text(.99, .001, f"统一单元视窗 {span:,.0f} × {span:,.0f} m · 编号对应明细CSV", ha="right", size=5 if compact else 7, transform=ax.transAxes, color="#667168")


def draw_summary(side, result, colors):
    ink, accent, muted = "#26352e", "#34584b", "#667168"
    side.set_axis_off();side.set_xlim(0, 1);side.set_ylim(0, 1)
    side.text(0, 1, "统计与口径", size=13, weight="bold", color=ink, va="top")
    kind = result["display_kind"]
    if kind == "density_grid":
        metric = f"{result['coverage_percent']:.2f}%"
        caption = "分析范围内建筑轮廓并集覆盖率"
        detail = f"网格边长 {result['grid_size_m']:g} m\n有效网格 {result['cell_count']} 格\n参与统计对象 {result['analysis_feature_count']} 个"
        rows = result["table"]
    elif kind == "orientation_rose":
        metric = f"{result['total_length_m']/1000:,.2f} km"
        caption = "按层级去重的道路线总长度"
        dominant = result["dominant_orientation_degrees"]
        detail = f"方位分箱 {result['bin_width_degrees']:g}°\n参与统计对象 {result['analysis_feature_count']} 个\n"+(f"最大方向箱中心 {dominant:g}°" if dominant is not None else "没有可统计方向")
        rows = sorted(result["table"], key=lambda row: -row["value"])[:6]
    else:
        metric = f"{result['analysis_feature_count']} 个"
        caption = "按面积降序选取的完整建筑轮廓"
        detail = f"符合条件的去重轮廓 {result['eligible_feature_count']} 个\n所选轮廓面积合计\n{result['total_area_m2']:,.0f} m²"
        rows = []
        side.text(0, .56, "编号可回到明细CSV的来源ID。\n\n卡片使用相同比例，\n保留原轮廓方向和孔洞。\n\n图录展示几何形态；\n用途、年代及结构未推断。", size=9, color=muted, linespacing=1.8, va="top")
    side.text(0, .89, metric, size=24, color=accent)
    side.text(0, .835, caption, size=8.6, color=muted)
    side.text(0, .775, detail, size=8.5, linespacing=1.7, color=ink, va="top")
    if rows:
        side.text(0, .61, "方向箱（长度前六）" if kind == "orientation_rose" else "各密度档内建筑轮廓面积", size=9, color=muted)
    for n, row in enumerate(rows):
        y = .54-n*.078
        side.plot([0, .05], [y, y], color=colors[row["category"]], lw=4)
        side.text(.085, y, row["category"], size=8, va="center", color=ink)
        number = f"{row['value']/1000:,.2f} km" if kind == "orientation_rose" else f"{row['value']/1e6:,.3f} km²"
        side.text(1, y-.028, number, size=7.3, ha="right", color=muted)
