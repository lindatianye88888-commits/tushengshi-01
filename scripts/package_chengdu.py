"""Package a successful three-map run and draw a clean comparison preview."""
from pathlib import Path
import csv
import json
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.catalog import read_json
from workflow.spatial import file_hash
from workflow.task import write_json
from workflow.render import font_setup, polygon_patches, draw_geometry
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection, LineCollection
from shapely.geometry import shape
from pyproj import Transformer

NAMES = {"fabric": "01-建筑城市肌理", "roads": "02-路网街道", "landuse": "03-用地功能"}
COLORS = {"居住用地":"#edce75", "商业服务":"#e78a94", "公共服务":"#9290ce", "绿地休闲":"#b5c994",
          "工业仓储":"#dbaf7a", "水域":"#8bbac8", "交通设施":"#aaa8a1", "其他已知用地":"#e2d4ad",
          "未知":"#d9d7cd", "建筑基底":"#334d45", "高速快速路":"#2e514f", "主干道":"#456963",
          "次干道":"#708b7c", "支路街道":"#9aaa99", "内部服务路":"#b4bdad", "步行与非机动车":"#d0ccbb"}


def draw_map(ax, result, did, center):
    display = shape(result["scopes"]["display"])
    draw_geometry(ax,display,"#eeeee6",width=0,fill=True)
    if did == "roads":
        lines, colors, widths = [], [], []
        rules = {"高速快速路":1.1, "主干道":.85, "次干道":.6,"支路街道":.4,"内部服务路":.25,"步行与非机动车":.2}
        for f in result["display_features"]:
            geom = shape(f["geometry"])
            parts = [geom] if geom.geom_type == "LineString" else list(geom.geoms) if hasattr(geom,"geoms") else []
            for part in parts:
                if part.geom_type == "LineString":
                    lines.append(list(part.coords)); colors.append(COLORS.get(f["category"],"#aaa8a1"));widths.append(rules.get(f["category"],.3))
        ax.add_collection(LineCollection(lines,colors=colors,linewidths=widths))
    else:
        patches=[p for f in result["display_features"] for p in polygon_patches(shape(f["geometry"]),COLORS[f["category"]])]
        ax.add_collection(PatchCollection(patches,match_original=True))
    draw_geometry(ax,display,"#adbaac",width=.6,fill=False)
    ax.plot(*center,marker="o",ms=4,mfc="#faf9f4",mec="#a34e43",mew=1.2,zorder=20)
    ax.annotate("万象城",center,xytext=(6,6),textcoords="offset points",size=7,color="#944e42",zorder=21,
                bbox={"facecolor":"#f7f6ef","edgecolor":"none","alpha":.8})
    x0,y0,x1,y1=display.bounds
    nx,ny=result["true_north_vector"]
    start=(x1-600,y1-2100)
    ax.annotate("N",xy=(start[0]+nx*600,start[1]+ny*600),xytext=start,
                ha="center",size=7,color="#425b4f",arrowprops={"arrowstyle":"-|>","lw":.9,"color":"#425b4f"})
    padding=(x1-x0)*.02
    ax.set_xlim(x0-padding,x1+padding);ax.set_ylim(y0-padding,y1+padding)
    ax.set_aspect("equal");ax.set_axis_off()


def main():
    run = Path(sys.argv[1]).resolve()
    manifest=read_json(run/"manifest.json")
    if len(manifest["instances"])!=3 or any(i["run_status"]!="needs_review" for i in manifest["instances"]):
        raise ValueError("Only package a successful three-instance run.")
    output=ROOT/"output/chengdu-mixc-5km/deliverables"
    output.mkdir(parents=True,exist_ok=True)
    results={}
    for item in manifest["instances"]:
        did=item["diagram_id"];name=NAMES[did]
        result=read_json(run/item["analysis_file"]["path"])
        results[did]=result
        for f in item["files"]:
            source=run/f["path"]
            if file_hash(source)!=f["sha256"]:raise ValueError("Export hash mismatch")
            shutil.copy2(source,output/f"{name}.{f['format']}")
        with (output/f"{name}-统计.csv").open("w",encoding="utf-8-sig",newline="") as handle:
            writer=csv.DictWriter(handle,fieldnames=["category","value","unit","share_percent","analysis_area_percent"])
            writer.writeheader();writer.writerows(result["table"])
    shutil.copy2(run/"task.snapshot.json",output/"task.json")
    shutil.copy2(run/"task-cards.md",output/"任务卡.md")
    # Preserve the original manifest with its path context in a named subfolder.
    write_json(output/"source-run.json",{"run_directory":str(run),"manifest_sha256":file_hash(run/"manifest.json")})
    summary={did:{k:r[k] for k in ("analysis_area_m2","analysis_feature_count","total_length_m","total_area_m2","coverage_percent","unmapped_area_m2")}
             for did,r in results.items()}
    write_json(output/"统计摘要.json",summary)
    fig=plt.figure(figsize=(18,8.4),facecolor="#f7f6ef")
    with plt.rc_context({"font.family":font_setup(),"axes.unicode_minus":False}):
        fig.text(.04,.94,"成都万象城周边 5 km",size=24,color="#2d473d",weight="bold")
        fig.text(.04,.89,"建筑城市肌理  /  路网街道  /  用地功能",size=12,color="#6a796f")
        fig.text(.96,.94,"CHENGDU  ·  MIXC",ha="right",size=11,color="#6a796f")
        fig.text(.96,.89,"同一中心  /  半径 5,000 m  /  米制投影",ha="right",size=10,color="#6a796f")
        location=manifest["project"]["location"]
        center=Transformer.from_crs(4326,32648,always_xy=True).transform(location["lon"],location["lat"])
        for n,did in enumerate(["fabric","roads","landuse"]):
            left=.025+n*.325
            ax=fig.add_axes([left,.24,.31,.62])
            draw_map(ax,results[did],did,center)
            fig.text(left+.155,.218,NAMES[did][3:],ha="center",size=15,color="#2d473d",weight="bold")
            r=results[did]
            note=(f"已收录轮廓 {r['analysis_feature_count']:,} 个 · 基底并集 {r['total_area_m2']/1e6:.2f} km²" if did=="fabric" else
                  f"道路线长度 {r['total_length_m']/1000:.1f} km · 含步行与服务道路" if did=="roads" else
                  f"已收录用途 / 水域 {r['coverage_percent']:.1f}% · 灰底为未收录或未分类")
            fig.text(left+.155,.174,note,ha="center",size=9,color="#69796f")
        legend=[(label,COLORS[label]) for label in ["居住用地","商业服务","公共服务","绿地休闲","工业仓储","水域","交通设施","其他已知用地"]]
        legend.append(("未收录 / 未分类","#dddcd3"))
        for n,(label,color) in enumerate(legend):
            x=.05+n*.1
            fig.text(x,.109,"■",color=color,size=12)
            fig.text(x+.015,.109,label,size=8.5,color="#65766c")
        fig.text(.04,.052,"来源：Overture Maps 2026-10-21.0 / © OpenStreetMap contributors / Qian Shi et al. (2023), doi:10.5281/zenodo.8174931 (CC BY 4.0)",size=8,color="#718076")
        fig.text(.04,.025,"开放地图已收录对象，现实完整性未核实；用地依据来源属性归类，未收录区域不推断用途。",size=8,color="#718076")
        fig.savefig(output/"三图预览.png",dpi=180,facecolor=fig.get_facecolor())
    plt.close(fig)
    (output/"说明.md").write_text(
        "# 成都万象城周边半径5公里三图\n\n"
        "范围为万象城OSM用地外包框中心附近的投影平面半径5,000米，不是步行5公里。中心为 WGS84 (104.1126387, 30.6507515)。\n\n"
        "数据来自 Overture 2026-10-21.0，原始来源包含 OpenStreetMap 等。此版本日期不等于每个对象的测绘日期。\n\n"
        "建筑还包含 Qian Shi 等 (2023) 的东亚建筑数据，Zenodo DOI: 10.5281/zenodo.8174931，CC BY 4.0（https://zenodo.org/records/8174931）。部分轮廓为遥感屋顶投影，不能视为测绘实测建筑基底。\n\n"
        "建筑覆盖率是已收录轮廓的并集面积占研究范围比例；空白不等于现实空地。道路线按来源标签映射分类，不是法定道路等级。双幅道路分别累计，含步行和服务道路；不表达交通流量或实际通行可达性。\n\n"
        "用地图为来源用途属性的现状色块分类，叠置处小面优先，不是法定规划用地。灰底为未收录或未分类。CSV中的面积与长度来自对应单图同一统计结果。\n\n"
        "SVG线面与文字可编辑，需有微软雅黑或等效中文字体；PDF为矢量，PNG为预览。完整输入资料留在项目目录，task.json为本次配置快照；相对数据路径以原项目目录为基准。\n\n"
        "地图署名：© OpenStreetMap contributors, Overture Maps Foundation；开放数据许可 ODbL-1.0。\n",
        encoding="utf-8")
    print(output,flush=True)


if __name__=="__main__":main()
