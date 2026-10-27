"""Maps with source points, explicit metric units and readable label indexes."""
import math
from matplotlib.collections import PatchCollection,LineCollection
from shapely.geometry import shape,Point

from .render import draw_map,draw_geometry,polygon_patches,wrapped
from .styles import PALETTES


def point_colors(result,style):
    if result['point_kind'] in {'poiheat','urbangrid'}:
        shades=['#eeeee5','#dce4cf','#b9cfa8','#89b58f','#52866e','#2e5c51'] if style.get('palette')!='monochrome' else ['#eeeee6','#d6dbdc','#b2babe','#8b969d','#5d6b75','#303f4d']
        return {row['category']:shades[n%len(shades)] for n,row in enumerate(result['table'])}
    palette=PALETTES['monochrome' if style.get('palette')=='monochrome' else 'atlas']
    cats=list(dict.fromkeys([row['category'] for row in result['table']]+[p['category'] for p in result.get('display_points',[])]))
    colors={category:('#b4b5af' if category=='分类未知' else palette[i%len(palette)]) for i,category in enumerate(cats)}
    if result['point_kind']=='voronoi':
        colors.update({'territory:'+row['site_id']:palette[i%len(palette)] for i,row in enumerate(result['detail_table'])})
    return colors


def summary_text(result):
    return result['headline']+' · '+('来源记录/km² · 高斯核估计' if result['point_kind']=='poiheat' else result['headline_caption'])


def plot_points(ax,records,colors,display,size=9,alpha=.85,zorder=4,outline=False):
    records=[r for r in records if display.covers(shape(r['geometry']))]
    if records:
        ax.scatter([r['geometry']['coordinates'][0] for r in records],[r['geometry']['coordinates'][1] for r in records],
                   c=[colors.get(r['category'],'#6a9b94') for r in records],s=size if isinstance(size,(int,float)) else [size(r) for r in records],alpha=alpha,zorder=zorder,edgecolors='#f5f4ee' if outline else 'none',linewidths=.4 if outline else 0)


def index_labels(ax,records,colors,compact):
    # Label positions are authored separately from immutable source coordinates.
    # Keep leader endpoints tied to source points and record complete names in CSV.
    left=[];right=[];middle=sum(ax.get_xlim())/2
    for r in records:
        (left if r['geometry']['coordinates'][0]<middle else right).append(r)
    for items,is_left in [(left,True),(right,False)]:
        items.sort(key=lambda r:(-r['geometry']['coordinates'][1],r['id']))
        for i,r in enumerate(items):
            text=str(r['number']) if compact else f"{r['number']:02d}  "+(r['name'][:14]+'…' if len(r['name'])>14 else r['name'] or '来源未命名')
            y=.92-(i+.5)*.84/max(1,len(items))
            ax.annotate(text,xy=r['geometry']['coordinates'],xycoords='data',xytext=(.02 if is_left else .98,y),textcoords='axes fraction',
                        ha='left' if is_left else 'right',va='center',size=6 if compact else 8,color='#3d6253',zorder=8,
                        bbox={'facecolor':'#f5f4ee','edgecolor':'none','alpha':.88,'pad':1.5},
                        arrowprops={'arrowstyle':'-','lw':.45,'color':'#9fae96'})


def draw_panel(ax,result,project,parameters,colors,compact=False):
    display=shape(result['scopes']['display']);did=result['point_kind']
    draw_map(ax,{**result,'diagram_id':'fabric','display_features':[]},project,{},colors,compact)
    cells=[];uncertain_patches=[]
    for f in result.get('display_cells',[]):
        geom=shape(f['geometry']).intersection(display)
        fill=colors.get('territory:'+f['id'],colors.get(f['category'],'#aabca1')) if did=='voronoi' else colors.get(f['category'],'#aabca1')
        for patch in polygon_patches(geom,fill,width=.45 if did=='voronoi' else .05):
            patch.set_edgecolor('#faf9f2')
            cells.append(patch)
        if f.get('edge_uncertain'):uncertain_patches.extend(polygon_patches(geom,'#929b88',width=.1,fill=False))
        if did=='voronoi' and not geom.is_empty:
            label=geom.representative_point()
            ax.text(label.x,label.y,str(f['number']),ha='center',size=6 if compact else 9,color='#35473b',zorder=7,bbox={'facecolor':'#f5f4ee','edgecolor':'none','alpha':.85,'pad':1})
    if cells:ax.add_collection(PatchCollection(cells,match_original=True,zorder=2))
    if uncertain_patches:ax.add_collection(PatchCollection(uncertain_patches,facecolor='none',edgecolor='#87917e',linewidths=.15,hatch='///',zorder=2.5))
    for geom in result.get('influence_circles',[]):draw_geometry(ax,shape(geom).intersection(display),'#d2dcbf',width=.35,fill=False)
    lines=[list(shape(g).coords) for g in result.get('relation_lines',[])]
    if lines:ax.add_collection(LineCollection(lines,colors='#b56f60',linewidths=.5,alpha=.7,zorder=3))
    plot_points(ax,result.get('context_points',[]),{},display,1.5 if compact else 3,alpha=.45,zorder=3)
    if did in {'urbanstations','nodes'}:
        max_count=max((r['neighbor_count'] for r in result['display_points']),default=1)
        size=lambda r:(10 if compact else 18)+(45 if compact else 100)*r['neighbor_count']/max(1,max_count)
        plot_points(ax,result['display_points'],colors,display,size,zorder=5)
        for r in result['display_points']:
            if not display.covers(shape(r['geometry'])):continue
            label=str(r['number']) if did=='nodes' else ''
            if label:ax.annotate(label,r['geometry']['coordinates'],xytext=(4,4),textcoords='offset points',size=6 if compact else 8,color='#8d514c',zorder=9)
    else:plot_points(ax,result.get('display_points',[]),{r['category']:'#36594e' for r in result.get('display_points',[])} if did=='voronoi' else colors,display,1.2 if did=='poiheat' else 6 if did=='voronoi' else 8 if compact else 14,alpha=.6 if did=='poiheat' else .9,outline=did=='voronoi')
    if did=='sitemap':index_labels(ax,[r for r in result['display_points'] if display.covers(shape(r['geometry']))],colors,compact)
    if did=='voronoi' and result['edge_uncertain_area_m2']:
        ax.text(.02,.98,'斜纹：取数边缘影响未保证',transform=ax.transAxes,va='top',size=6 if compact else 8,color='#69725f',bbox={'facecolor':'#f5f4ee','edgecolor':'none','alpha':.9},zorder=10)


def draw_summary(side,result,colors):
    side.set_axis_off();side.set_xlim(0,1);side.set_ylim(0,1)
    side.text(0,1,'设施与表达口径',size=13,color='#2e493a',weight='bold',va='top')
    side.text(0,.90,result['headline'],size=21,color='#345b48')
    side.text(0,.84,wrapped(result['headline_caption'],40),size=8.5,color='#6f776b',va='top',linespacing=1.6)
    did=result['point_kind']
    extra=(f"带宽 {result['bandwidth_m']:g} m · 采样格 {result['cell_size_m']:g} m\n核支持半径 {result['support_radius_m']:g} m" if did=='poiheat' else
           f"有效网格 {result['grid_count']} 格\n颜色表示每格点数；保留零格" if did=='urbangrid' else
           f"参与竞争点组 {result['site_group_count']} 组\n边缘未保证 {result['edge_uncertain_area_m2']/1e6:.2f} km²" if did=='voronoi' else
           f"符号面积表示 {result['radius_m']:g} m 内其他设施数\n保留来源类别，未经运营核实" if did=='urbanstations' else
           f"圈为 {result['radius_m']:g} m 统计半径\n联系线为项目直线距离" if did=='nodes' else
           f"候选 {result.get('candidate_count',result['source_point_count_in_analysis'])} 条来源设施\n完整名称、类别及来源ID见明细")
    if result.get('quality_flags'):extra+=f"\n{len(result['quality_flags'])} 条名称/类别待核对"
    side.text(0,.74,extra,size=8.3,color='#596a5d',va='top',linespacing=1.7)
    if did in {'sitemap','voronoi'}:
        for i,r in enumerate(result['detail_table']):
            if i>=12:
                side.text(0,.075,'其余分区及完整名称见明细',size=7,color='#758170');break
            y=.60-i*.041
            label=r.get('name',r.get('names')) or '来源未命名';label=label[:13]+'…' if len(label)>13 else label
            if did=='voronoi':
                side.plot([0,.04],[y,y],lw=5,color=colors['territory:'+r['site_id']])
                label=('外部 ' if not r['seed_inside_analysis'] else '')+label
            side.text(.065 if did=='voronoi' else 0,y,f"{r['number']:02d} {label}",size=7,color='#3c5142')
            value=f"{r['straight_distance_m']:,.0f} m" if did=='sitemap' else f"{r['cell_area_m2']/1e6:.2f} km²"
            side.text(1,y-.015,value,size=6.5,ha='right',color='#758170')
        return
    rows=result['table'];step=min(.057,.55/max(1,len(rows)))
    for i,row in enumerate(rows):
        y=.61-i*step
        side.plot([0,.05],[y,y],color=colors.get(row['category'],'#66866c'),lw=4)
        side.text(.09,y,row['category'],size=7.5,color='#3c5142',va='center')
        value=f"{row['value']/1e6:.2f} km²" if row['unit']=='m²' else f"{row['value']:g} {row['unit']}"
        side.text(1,y-.020,value,size=6.8,color='#6f776b',ha='right')
