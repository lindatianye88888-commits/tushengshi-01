PALETTES = {
    "atlas": ["#edce75","#e78a94","#9290ce","#b5c994","#dbaf7a","#8bbac8","#aaa8a1","#e2d4ad","#c1add1","#dfc6a2","#a7bdb4","#beb7aa","#91a4ab","#b4bf90","#bd9684","#acb1cb"],
    "grey_green": ["#34584b","#6d8d79","#a8baa6","#6c99ab","#bfaf8f","#717e83","#b4c8ce","#938b72","#b2a1b5","#d1ba9d","#7f9c9b","#a9a39a","#466c76","#b2b579","#c2917c","#9293ac"],
    "monochrome": ["#283039","#505b66","#7c8793","#a5adb5","#c7cdd2","#60676c","#8c969a","#b6bbbe","#3c454e","#91969b","#d7dadd","#6c7379","#adb4bc","#77818b","#b7c0c7","#d1d3d6"]}
SEMANTIC_COLORS = {"居住用地":"#edce75","商业服务":"#e78a94","公共服务":"#9290ce","绿地休闲":"#b5c994",
    "绿地":"#b5c994","green":"#b5c994","水域":"#8bbac8","water":"#8bbac8","工业仓储":"#dbaf7a",
    "交通设施":"#aaa8a1","其他已知用地":"#e2d4ad","未知":"#d9d7cd","建筑基底":"#334d45","建筑轮廓":"#334d45",
    "高速快速路":"#2e514f","主干道":"#456963","次干道":"#708b7c","支路街道":"#9aaa99","内部服务路":"#b4bdad","步行与非机动车":"#d0ccbb"}
ROAD_WIDTHS={"高速快速路":1.8,"主干道":1.35,"次干道":.85,"支路街道":.55,"内部服务路":.35,"步行与非机动车":.25}
TITLES={"fabric":"建筑城市肌理","roads":"路网与街道","landuse":"用地功能分布","gw":"绿地与水体",
        "density":"建筑轮廓覆盖率","orientrose":"街道方向玫瑰图","typology":"建筑轮廓类型学图录",
        "context":"城市区位","location":"多级行政区位","orient":"真北与方位参考","pedestrian":"步行通行属性底图",
        "combined":"综合图层叠加","fabricplus":"城市肌理概化","connectionfield":"路口几何关联场",
        "poitype":"设施来源类型","sitemap":"周边设施标注索引","poiheat":"设施来源记录核密度","voronoi":"设施欧氏最近分区",
        "urbangrid":"设施点数网格","urbanstations":"站点与周边设施","nodes":"设施集中度节点"}


def effective_style(task,item):
    return {"palette":"grey_green","page":"A3","language":"zh",**task.get("shared_style",{}),**item.get("style",{})}


def validate_style(style):
    if not isinstance(style,dict): raise ValueError("style 需要对象。")
    extra=set(style)-{"palette","page","language","status"}
    if extra: raise ValueError(f"尚不支持的样式字段：{sorted(extra)}")
    if style.get("palette","grey_green") not in PALETTES: raise ValueError("未知色板，可选 atlas / grey_green / monochrome。")
    if style.get("page","A3") not in {"A3","A4"} or style.get("language","zh")!="zh":
        raise ValueError("当前支持 A3 / A4 横版中文图框。")
    return style


def diagram_colors(result,style):
    if result.get('point_kind'):
        from .points_render import point_colors
        return point_colors(result,style)
    if result.get('cartographic_kind'):
        colors=dict(result['legend_colors'])
        if style.get('palette')=='monochrome':
            shades=PALETTES['monochrome'];colors={k:shades[n%len(shades)] for n,k in enumerate(colors)}
        return colors
    if result.get("display_kind")=="density_grid":
        shades = (["#f1f0e7","#d4d8d9","#abb4b9","#828e97","#566571","#293d4a"] if style.get("palette")=="monochrome"
                  else ["#f1f0e7","#dce5ce","#b9cfaa","#8daf88","#5f8868","#355c49"])
        return {row["category"]: shades[n] for n, row in enumerate(result["table"])}
    if result.get("display_kind") in {"orientation_rose","typology_catalog"}:
        ink = "#38434d" if style.get("palette")=="monochrome" else "#3a6250"
        return {row["category"]: ink for row in result["table"]}
    palette=PALETTES[style.get("palette","grey_green")]
    colors={row["category"]:("#b4b4b4" if row["category"]=="未知" else palette[n%len(palette)]) for n,row in enumerate(result["table"])}
    if style.get("palette")=="atlas": colors={key:SEMANTIC_COLORS.get(key,value) for key,value in colors.items()}
    return colors
