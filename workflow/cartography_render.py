"""Draw source-backed context panels, access inventories and graphic overlays."""
import math

from matplotlib.collections import PatchCollection, LineCollection
from shapely.geometry import shape

from .render import draw_map, draw_geometry, polygon_patches


def summary_text(result):
    return result["headline"]+" · "+result["headline_caption"]


def draw_layers(ax,layers,colors,compact=False):
    for layer in layers:
        patches=[];lines=[];line_colors=[];widths=[];styles=[];points=[];point_colors=[]
        for feature in layer['features']:
            geom=shape(feature['geometry']);color=colors.get(feature['category'],'#73947c')
            if feature.get('kind') in {'water','水域'}: color=colors.get('水面',color)
            if geom.geom_type in {'Polygon','MultiPolygon'}:
                patches.extend(polygon_patches(geom,color,width=0))
            elif geom.geom_type in {'LineString','MultiLineString'}:
                parts=[geom] if geom.geom_type=='LineString' else list(geom.geoms)
                for part in parts:
                    lines.append(list(part.coords));line_colors.append(color)
                    widths.append((.24 if layer['id']=='neighbors' else .22 if feature['category']=='通行未知' else .55)*(.7 if compact else 1))
                    styles.append('dotted' if feature['category']=='通行未知' else 'dashed' if feature['category'] in {'来源明确禁止','条件待核对'} else 'solid')
            elif geom.geom_type=='Point':
                points.append((geom.x,geom.y));point_colors.append(color)
        if patches: ax.add_collection(PatchCollection(patches,match_original=True))
        if lines: ax.add_collection(LineCollection(lines,colors=line_colors,linewidths=widths,linestyles=styles))
        if points: ax.scatter([p[0] for p in points],[p[1] for p in points],c=point_colors,s=2 if compact else 4,zorder=4,linewidths=0)


def map_frame(ax,result,project,colors,compact):
    draw_map(ax,{**result,'diagram_id':'fabric','display_features':[]},project,{},colors,compact)


def inset_boundary(ax,panel,center,scope,compact=False,roads=None,colors=None):
    geom=shape(panel['geometry']);x0,y0,x1,y1=geom.bounds
    pad=max(x1-x0,y1-y0)*.08
    draw_geometry(ax,geom.simplify(max(x1-x0,y1-y0)/2000,preserve_topology=True),'#c8d4bb',width=.6)
    ax.plot(center[0],center[1],'o',ms=4 if compact else 5,mfc='#b66753',mec='#faf8ef',zorder=10)
    ax.set_xlim(x0-pad,x1+pad);ax.set_ylim(y0-pad,y1+pad);ax.set_aspect('equal');ax.set_axis_off()
    ax.set_title(panel['label'],fontsize=8 if compact else 11,color='#365544',pad=5)
    target=max(x1-x0,y1-y0)*.25
    exponent=10**math.floor(math.log10(target))
    scale=next((n*exponent for n in (5,2,1) if n*exponent<=target),exponent/2)
    sx,sy=x0,y0-pad*.6
    ax.plot([sx,sx+scale],[sy,sy],lw=1,color='#526b5a')
    ax.text(sx,sy+pad*.1,f'≈{scale/1000:g} km',fontsize=5 if compact else 7,color='#526b5a')


def draw_panel(ax,result,project,parameters,colors,compact=False):
    did=result['cartographic_kind']
    if did in {'context','location'}:
        ax.set_axis_off();panels=result['panels']
        count=len(panels)+(1 if did=='context' else 0)
        for n,panel in enumerate(panels):
            child=ax.inset_axes([n/count+.02,.12,.94/count,.78])
            inset_boundary(child,panel,result['center'],result['scopes']['analysis'],compact)
        if did=='context':
            child=ax.inset_axes([.52,.12,.46,.78])
            map_frame(child,result,project,colors,compact)
            draw_layers(child,result['display_layers'],colors,compact)
            child.set_title('已取数的本地范围',fontsize=8 if compact else 11,color='#365544')
        return
    if did=='orient':
        ax.set_axis_off();ax.set_xlim(-1.4,1.4);ax.set_ylim(-1.3,1.3);ax.set_aspect('equal')
        from matplotlib.patches import Circle
        ax.add_patch(Circle((0,0),1,fill=False,edgecolor='#b1bea9',lw=1))
        ax.plot([0,0],[-1,1],color='#c3c7bc',ls='--',lw=.8)
        nx,ny=result['true_north_vector']
        for bearing,label in [(0,'N 真北'),(90,'E'),(180,'S'),(270,'W')]:
            a=math.radians(bearing);x=nx*math.cos(a)+ny*math.sin(a);y=ny*math.cos(a)-nx*math.sin(a)
            ax.annotate('',xy=(x,y),xytext=(0,0),arrowprops={'arrowstyle':'-|>','color':'#3e6551','lw':1.3})
            ax.text(x*1.16,y*1.16,label,ha='center',va='center',size=8 if compact else 11,color='#3e6551')
        axis=result.get('axis_bearing_deg')
        if axis is not None:
            a=math.radians(axis);x=nx*math.cos(a)+ny*math.sin(a);y=ny*math.cos(a)-nx*math.sin(a)
            ax.plot([-x*.9,x*.9],[-y*.9,y*.9],color='#a56d52',lw=2)
        ax.text(0,-1.28,'未给定项目主轴' if axis is None else f'人工方位 {axis:g}°',ha='center',size=7 if compact else 10,color='#8b6a53')
        return
    map_frame(ax,result,project,colors,compact)
    draw_layers(ax,result['display_layers'],colors,compact)


def draw_summary(side,result,colors):
    side.set_axis_off();side.set_xlim(0,1);side.set_ylim(0,1)
    side.text(0,1,'表达与数据口径',size=13,color='#2e493a',weight='bold',va='top')
    side.text(0,.89,result['headline'],size=23,color='#345b48')
    caption=result['headline_caption']
    if len(caption)>22: caption=caption[:22]+'\n'+caption[22:]
    side.text(0,.825,caption,size=8.5,color='#6f776b',va='top',linespacing=1.6)
    did=result['cartographic_kind']
    extra=(f"原轮廓面积 {result['original_area_m2']/1e6:.3f} km²\n概化图形面积 {result['generalized_area_m2']/1e6:.3f} km²\n概化仅用于表达" if did=='fabricplus' else
           f"几何近邻连线 {result['edge_count']} 条\n近邻线不表示实际通行" if did=='connectionfield' else
           '主轴来源：'+str(result.get('axis_source') or '未提供；不推断') if did=='orient' else
           '各级边界均由来源行政面提供\n大尺度比例尺为投影近似' if did in {'context','location'} else
           f"统计对象 {result['analysis_feature_count']} 个")
    side.text(0,.72,extra,size=8.5,color='#596a5d',va='top',linespacing=1.8)
    for n,row in enumerate(result['table'][:6]):
        y=.53-n*.075
        side.plot([0,.05],[y,y],color=colors.get(row['category'],'#66866c'),lw=4)
        side.text(.09,y,row['category'],size=8,color='#3c5142',va='center')
        value=f"{row['value']/1000:.2f} km" if row['unit']=='m' else f"{row['value']/1e6:.3f} km²" if row['unit']=='m²' else f"{row['value']:+.3f}°" if row['unit']=='°' else f"{row['value']:g} {row['unit']}"
        side.text(1,y-.028,value,size=7.5,color='#6f776b',ha='right')
