# 图省事 01｜建筑景观规划·分析图工作流

**署名：梅梅的AI景观实验室**

当前为 v0.4：原50类排期中，**14类有明确可运行模式，36类待开发**。图类登记不代表所有扩展模式均已完成。完整的[开发思路、批次及验收计划](docs/development-plan.md)和[机器可读排期](catalog/development-plan.json)已建立。主入口是 [跨端操作说明](WORKFLOW.md)，通过请求文件、任务卡、资料获取、计算、预览、验收和成果包完成一次出图。当前 Codex 环境已实测；WorkBuddy、豆包等客户端通过同一文件与命令协议使用，是否可直接调用取决于其本地工具权限。

需要 Python 3.11 及以上版本和中文字体。本地已在 Windows / Python 3.12 验证。首次下载并安装（包含公开地图取数依赖，不需要模型API密钥）：

```powershell
git clone https://github.com/lindatianye88888-commits/tushengshi-01.git
cd tushengshi-01
python -m pip install -r requirements-data.txt
python -m workflow doctor
```

中文字体可用微软雅黑、思源黑体或 Noto Sans CJK SC。只使用已有本地资料时，可安装更小的 `requirements.txt`。需要独立环境时可先建立并激活 Python 虚拟环境。

AI 只需填写 [简短请求示例](examples/request.example.json)，中心点和半径由执行器转换成实际空间范围；文件格式见 [请求 Schema](schemas/request.schema.json)。先复制示例：

```powershell
New-Item -ItemType Directory -Force projects/current | Out-Null
Copy-Item examples/request.example.json projects/current/request.json
```

请按实际项目修改请求中的位置、范围和选图。示例默认 `requirements_confirmed=false`；确认已有用户委托后，填写真实的 `authorization_note` 并设为 `true`，再执行：

```powershell
python -m workflow start projects/current/request.json
```

结果位置会写入同目录 `request.result.json`；成果在 `projects/current/output/运行编号/`，含 `preview.png`、逐图文件、统计及来源记录。公开数据提取需要可访问地图服务的网络；缓存及项目资料不随代码仓库提供。

没有命令工具时，可将已保存的请求文件拖到“运行工作流.cmd”。已有完整任务也可以使用 `python -m workflow run 任务文件 --out 输出目录`，或拖到“运行出图.cmd”。

基础四图为道路、建筑肌理、用地功能、绿地水体。v0.3新增建筑密度、街道方向玫瑰图、建筑类型学图录，分别支持网格轮廓覆盖率、按长度加权的无向道路方位、按面积降序的统一比例轮廓图录，见[空间统计请求示例](examples/request.statistics.json)。

v0.4新增城市区位、省—市—区定位、真北与人工方位参考、步行通行属性底图、三层叠加、道路水面隔断下的肌理概化、来源路口的几何关联场，见[制图请求示例](examples/request.cartography.json)。步行图不计算可达路径，概化图尚无街名，定位图不含国家尺度；支持模式及参数见 [WORKFLOW.md](WORKFLOW.md)。SVG/PNG已实测；基础四图另有矢量PDF验证，新增图的PDF版面待单独核对。其余36类保留需求，不执行替代算法。

成都万象城周边半径5公里的四图已在本地完成实际验证，数据固定为Overture 2026-10-21.0。真实项目资料、图片、成果包和原始缓存不上传到代码仓库；下载仓库后需重新获取自己的资料，或导入另行提供的复跑包。

程序按图纸分别检查需求确认、资料齐备和实现状态。每次运行保存冻结输入、配置、逐图分析、统计CSV、数据哈希、算法与绘图版本及待验收清单。支持从既有结果重绘、记录修改意见与核对人，以及制作含数据和执行器的跨机器复跑包。缺资料不填零值，开放地图的取数完整与现实完整分开记录。

- [v0.2 模块与接入范围](docs/implementation-v02.md)
- [v0.2 验证结果与实测边界](docs/validation-v02.md)
- [v0.3 新增三类的验证记录](docs/validation-v03.md)
- [v0.4 七类模式、真实案例及限制](docs/validation-v04.md)
- [REA公开行为核对与支持范围差异](docs/reference-p2a-rea.md)
- `python -m workflow preview 运行目录`：生成自动总览。
- `python -m workflow repaint 运行目录 --palette monochrome`：复用计算重绘。
- `python -m workflow audit 运行目录`：检查完整性与统计合计。
- `python -m workflow bundle 运行目录 --out 成果包.zip`：带配置、数据和代码迁移。

开发检查：`python -m pip install -r requirements-dev.txt`，然后执行 `python -m unittest discover -s tests -v`。GitHub Actions在推送和PR时执行环境、语法、全部请求示例Schema和48项离线回归检查，不获取真实场地资料。

研究目录以2026-10-25核对的MapToNap英文目录为参照：50类图纸成果；另有7项Deep Analysis研究。2026-10-26经REA观察，官网已增加至51类（新增Urban Growth）；本项目保留原50类开发范围，详见[本次证据与差异](docs/reference-p2a-rea.md)。图纸需求按输入、关键口径、建议算法、表达、验收和解读边界拆开。

- 已认可的结构：50图分8组，以组级任务卡组织需求。共用设置只填一次，按已选图纸展开必要参数；具体卡片字段仍为设计草案。

- [八组任务卡：主要需求入口](docs/group-task-cards.md)
- [机器可读的组级任务卡](catalog/group-task-cards.json)
- [50项逐图需求与生成规格](docs/50-diagram-requirements.md)
- [选图与需求说明如何共同形成任务](docs/workflow-spec.md)
- [机器可读图纸目录](catalog/diagrams.json)
- [7项扩展研究规格](catalog/deep-analysis.json)
- [三维、概念、流线与平剖等其他工具范围](docs/product-scope.md)
- [网站证据、变更与尚未验证的部分](docs/research-evidence.md)
- [出图任务配置示例](examples/task-plan.example.json)

GitHub保存代码、模板和研究规格；真实项目资料和生成成果在被忽略的项目工作目录中维护。`scripts/`保留早期成都试跑脚本，当前通用执行器不依赖它们。

本目录保存的是独立实现的建议规格，没有复制网站的绘图实现。官方免费示例用于功能核对，不能称为本项目已实现的成果。
