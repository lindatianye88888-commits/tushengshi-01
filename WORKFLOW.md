# 图省事 01｜建筑景观规划·分析图工作流 v0.5

**署名：梅梅的AI景观实验室**

本项目把“对话整理需求”和“本地计算出图”接在一起。AI 桌面端负责理解用户要求、维护任务和解释结果；执行器负责范围、资料检查、空间计算、绘图及成果记录。当前21类有明确可运行模式，原50类范围内另有29类待开发。图类已实现某个模式，不代表研究目录中的全部参数均可用。完整顺序及验收门槛见 [开发计划](docs/development-plan.md)。

v0.3新增模式：`density`为`mode=coverage`，按规则网格统计建筑轮廓并集覆盖率；`orientrose`为`weighting=length`，逐线段按长度统计真实北向方位；`typology`为`sort_by=area_desc`与`same_scale=true`，完整轮廓按面积排序后用相同比例绘制。其他模式明确报缺项；参数示例见 `examples/request.statistics.json`。新增图的逐格/方向箱/建筑明细另存 `details.csv`，并纳入完整性检查、重绘和打包。

v0.4新增七类的范围见下表，请从 [制图请求示例](examples/request.cartography.json) 开始。SVG/PNG为本批实际核对格式；PDF版面尚未单独验收。

| 图类 | 已支持模式 | 解释边界 |
|---|---|---|
| 城市区位 `context` | `administrative_context`：真实行政面与本地道路线两个视窗 | 只显示实际获取的本地道路，不补画全城路网 |
| 区位分析 `location` | `nested_boundaries`：所提供的省、市、区等范围定位 | 以来源subtype选择层级；不含国家尺度，不能当法定边界图 |
| 场地朝向 `orient` | `true_north`：真北及可选人工方位 | 主轴需要角度和来源；圆形研究范围不产生主轴 |
| 步行网络 `pedestrian` | `source_access`：来源通行属性底图 | 明确允许、禁止、条件待核对、未知四类；不构建可达路网 |
| 综合叠加 `combined` | `layer_overlay`：按指定顺序叠加建筑、道路、绿水 | 各层分别记录数量，不生成综合评分 |
| 增强肌理 `fabricplus` | `road_water_barriers`：道路与水面隔断下合并建筑轮廓 | 概化图形面积不同于原轮廓面积；尚无街名自动标注 |
| 路网关联场 `connectionfield` | `source_connectors`：共享来源连接点识别路口，绘制几何近邻 | 几何交叉不自动连通，近邻线不表示实际通行或流量 |

本项目为独立实现；与参考产品的公开行为比较见 [REA记录](docs/reference-p2a-rea.md)。七类付费图未在参考产品中实际生成，不能声称算法或图面完全等价。官网现有51类，本项目继续执行已建立的历史50类排期。

v0.5新增七类使用[设施请求示例](examples/request.points.json)。SVG/PNG已核对，PDF版面仍待单独验收；与参考产品的公开差异见[P2B REA记录](docs/reference-p2b-rea.md)。

| 图类 | 已支持模式 | 解释边界 |
|---|---|---|
| 设施类型 `poitype` | `source_categories`：来源taxonomy分类 | 缺失分类保留未知，不按名称或建筑形态猜用途 |
| 设施标注 `sitemap` | `nearest_index`：最近设施的名称引线、编号及距离索引 | 按项目点投影直线距离；长名称图中缩写，明细保留全名 |
| 设施热力 `poiheat` | `gaussian_kde`：截断并归一化的二维高斯核 | 单位为来源记录/km²；采样值不是格内点数、人流或客流 |
| 服务范围 `voronoi` | `euclidean_partition`：欧氏最近点分区 | 区外竞争点参与、共址共用一格；边缘未保证区域斜纹标识；不判断服务缺口或路径可达性 |
| 城市网格 `urbangrid` | `point_count`：固定原点米制网格点数 | 每点只归一格，保留零格；明细另给有效面积及密度 |
| 城市节点 `urbanstations` | `source_stations`：来源明确站点、设施背景及周边记录数 | 不猜站点、整合入口、补造线路或推断实时运营、客流 |
| 节点分析 `nodes` | `local_concentration`：按周边设施数及间距选择真实点 | 圈为计数半径，联系线为项目直线；不是城市主轴或路网中心性 |

共同参数：`categories=[]`表示全部来源类别，可填taxonomy层级或主类，如`education`、`hospital`。默认`min_confidence=0.5`、`unknown_confidence=include`、`exclude_permanently_closed=true`；未知置信度不填零，临时停业不代表当前时刻营业状态。`deduplicate=true`只合并同坐标、同规范化非空名称及同主类记录，保留全部来源ID，不合并邻近商户或不同名称的共址对象。`selection_audit`保存筛选过程；`exclude_ids`供明确排除已核对对象。

热力默认100米采样格、150米带宽、3σ截断，需要至少450米取数缓冲；站点及节点默认300米统计半径，也检查缓冲。示例`padding_m=10000`使取数半径为15公里，分析与显示保持5公里，为稀疏医院类分区补充竞争点。最近分区仍逐格保守检查范围外竞争影响，不能因使用固定缓冲就保证所有场地正确；来源漏收完整性另外记录。

来源名称和类别可能错标。当前对医院类名称含站点词的记录保存`quality_flags`、明细和图面提示，不据文字自动改类；这只是有限启发式，不能发现全部错误。不能把开放地图空白称为设施不存在或服务短缺。

## 在不同桌面端使用

- 有本地文件和命令工具：让 AI 阅读本文件，运行 `python -m workflow doctor` 后操作。
- 能生成或保存文件、不能调用命令：让 AI 按 `examples/request.example.json` 生成请求，保存到项目文件夹，再将该文件拖到根目录的“运行工作流.cmd”。
- 只有聊天能力：可以整理请求内容，执行时需要本地启动器或具备工具权限的客户端。

以上是文件与命令协议，不代表已经给 WorkBuddy、豆包或其他客户端安装了插件或授予系统权限。当前 Codex 环境已完成本地实测，其他客户端应先核对其实际工具能力。

可直接交给 AI 的提示：

> 阅读这个项目的 WORKFLOW.md。根据我已有的选图和描述维护请求及任务卡，检查资料后执行并展示 PNG。保留我的明确选择和拒绝记录；推荐先放候选区。已有授权可以直接推进，只追问会改变成果含义且无法从已有信息确定的缺项。

## 入口一：请求文件

以 `examples/request.example.json` 为模板；字段说明见 `schemas/request.schema.json`。主要填写项目名、经过核对的 WGS84 中心点、半径、图纸、风格和格式。执行器自动选择适用的 UTM 投影并生成范围，AI 不需要手写上千个几何坐标。

```powershell
python -m workflow start projects/current/request.json
```

执行结果写到同目录 `request.result.json`；任务在 `task.json`，任务卡在 `review/task-cards.md`。可执行的成果会写入 `output/运行编号/` 并自动生成 `preview.png`。

`requirements_confirmed=true` 需要附真实的 `authorization_note`，记录用户已经确认或委托 AI 自行决定的依据；示例默认是 false。已有授权时不用重复向用户索取确认。程序不会因为示例里有参数就假定它们已获采纳。

请求中的 `diagrams` 默认合并新增选择；删图需显式填写 `remove_instances`。未修改的请求文件重复执行不会重置任务；手工改动了分析参数后，旧请求的确认不会自动覆盖新变更。目的可以留空。

`data.mode=overture` 按当前选图取公开资料；`data.mode=existing` 使用任务已登记的资料。`offline=true` 只使用指定缓存，范围不够就报告缺项。缓存有版本和范围检查，每次资料快照内固定同一 release。遥感轮廓、未知用途及未核实的现实完整性会保留说明。

v0.4为步行属性和路网关联获取完整的道路连接点、通行限制及其区段属性，并新增独立连接点和行政面资源。旧版仅含等级/名称的道路缓存不能满足这两类图，执行器会要求重新取数。道路与连接点使用原始完整几何对应线性引用，绘图和统计时再按各自范围裁剪。

## 入口二：直接维护任务

首次可运行 `python -m pip install -r requirements.txt` 安装绘图依赖；远端取数另需 `requirements-data.txt`。本机已有依赖时无需重复安装。出图需要微软雅黑、思源黑体或 Noto Sans CJK SC 字体。

```powershell
python -m workflow doctor
python -m workflow init projects/current/task.json --name "项目名称" --lon 104.1126387 --lat 30.6507515 --radius-m 5000 --label "场地中心" --diagrams fabric roads landuse gw
python -m workflow fetch projects/current/task.json
python -m workflow plan projects/current/task.json
python -m workflow confirm projects/current/task.json
python -m workflow run projects/current/task.json --out output/current
```

上例坐标只是示范。位置未核对时不能照抄为新项目；高德和百度坐标不能直接当作 WGS84。`confirm` 用于记录已明确的需求口径，不代替资料检查。默认按“项目共用信息 → 启用的组级任务卡 → 按选图补参数 → 出图清单”组织。

任务可以从单张图开始，只有选中图纸的组被展开。分析目的不强制填写。常用修改：

```powershell
python -m workflow select projects/current/task.json roads
python -m workflow propose projects/current/task.json access --reason "建议研究入口步行范围"
python -m workflow reject projects/current/task.json --instance access-01
python -m workflow remove projects/current/task.json --instance roads-01
python -m workflow style projects/current/task.json --palette monochrome
python -m workflow scope projects/current/task.json --lon 104.1126387 --lat 30.6507515 --radius-m 2000
```

同一图类重复选择会合并；另设情景可用 `--instance roads-north`。`fork 源任务 目标任务 --name 新项目名` 可以复用已有资料，目标文件存在时不会覆盖。

组级参数按适用图类继承；可用 `applies_to` 限定生效图类。逐图参数覆盖组设置时，任务卡会显示差异。需求确认、资料齐备、算法实现、运行结果分别记录，未实现图不会阻塞其他可执行图纸。

## 重绘、预览与验收

单次运行会保存数据快照、任务、计算结果、统计 CSV、SVG / PNG / 矢量 PDF 和导出哈希。相同数据按哈希复用存储。只改色板、纸张、标题或路名显示会复用相关计算；范围、分类、排除对象等变化则重新计算受影响实例。

从已有结果重绘，无需原始项目文件仍在线：

```powershell
python -m workflow repaint output/current/运行编号 --palette monochrome
python -m workflow repaint output/current/运行编号 --instance landuse-01 --page A4
python -m workflow preview output/current/运行编号
python -m workflow audit output/current/运行编号
```

重绘产生新运行目录，保留原记录。只修改一张图时，其他实例的成果和已记录验收状态可以复用。

AI 或用户查看实际 PNG 后，可以记录验收或修改意见：

```powershell
python -m workflow review output/current/运行编号 --reviewer "核对人或客户端" --decision accept --note "实际完成的核对事项"
python -m workflow review output/current/运行编号 --reviewer "核对人或客户端" --decision changes_requested --note "需要修改的具体位置"
```

文件缺失、哈希变更或统计合计不一致时，验收会被阻止。程序生成的 `needs_review` 不等于已完成视觉核对。验收 `complete` 只表示这次出图工作流已记录通过，不表示现场实测、法定规划或专业审批结论。

## 打包与跨机器复跑

```powershell
python -m workflow bundle output/current/运行编号 --out output/current-成果与工作流.zip
```

默认包含规范化输入、来源信息、任务、图纸、统计、冻结运行记录、执行器和目录。解压后的根目录可直接执行 `python -m workflow run task.json --out rerun-output`，不需要重新请求地图数据。`--without-inputs` 为轻量成果包，需要另补资料才可复跑。

机器读取可使用 `catalog --json`、`check 任务文件 --json`、`audit 运行目录` 和 `request.result.json`。这些接口可以由不同 AI 桌面端调用，无需模型 API 密钥。

## 数据和解读边界

显示、分析、取数范围分别保存；取数范围覆盖另两者。范围变更后，旧资料覆盖不足会提示重新取数。当前支持局地半径至50公里、南纬80度至北纬84度之间的米制投影场地；跨日期变更线的自动取数尚未支持。

本地资料用 FeatureCollection 导入，各对象需唯一 id；道路为线，建筑、用地、绿水、行政范围为面，连接点、`pois`与`transit_stops`为Point。设施属性使用`name`、`taxonomy.primary/hierarchy`、可选`basic_category`、`confidence`及`operating_status`；站点另需明确来源站点分类。每份资料登记来源、时段、坐标、单位、覆盖范围、完整性和估算字段。空资料须明确核实后才作为来源内零对象处理；这不表示现实没有对象。多资源图保存全部输入哈希；朝向图只依赖任务坐标及配置。Places逐来源署名与许可文件随成果包保存，见[catalog/licenses](catalog/licenses/README.md)。

`complete/source_complete` 指资料提取或提供状态，`mapping_completeness` 另记现实收录完整性。用地属性未知保留未知，不从建筑外形猜用途。地块用地与建筑功能分别选择 `mode=parcels/building_functions`。绿水图可以显式选用 `water_priority`，从绿地中扣除重叠水面；所有规则进入任务和分析记录。

原始研究目录及50图需求仍在 `docs/` 和 `catalog/`；下一批算法按这些规格分组接入，不能把目录存在等同于算法已实现。
