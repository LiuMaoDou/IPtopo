# IPtopo

[![CI](https://github.com/LiuMaoDou/IPtopo/actions/workflows/ci.yml/badge.svg)](https://github.com/LiuMaoDou/IPtopo/actions/workflows/ci.yml)
[![Windows executable](https://github.com/LiuMaoDou/IPtopo/actions/workflows/windows.yml/badge.svg)](https://github.com/LiuMaoDou/IPtopo/actions/workflows/windows.yml)

**从 Huawei、Cisco、Juniper、Nokia 配置与 LSDB 还原可解释、可编辑的 IP 逻辑拓扑，分析 OSPF / IS-IS 路径，并通过 AI 对话查询网络事实。**

Network topology workbench for Huawei, Cisco, Juniper and Nokia: configuration and LSDB visualization, OSPF/IS-IS path simulation, and AI-assisted analysis.

[下载 Windows x64 便携包](https://github.com/LiuMaoDou/IPtopo/releases/latest/download/IPtopo-Windows-x64.zip) · [版本与校验文件](https://github.com/LiuMaoDou/IPtopo/releases) · [快速开始](#快速开始) · [文档导航](#文档导航)

可在本机或可信内网部署。配置解析、拓扑编辑、路径计算和数据保存均在本地完成；AI 对话为可选功能，连接使用者配置的 Agent 或模型服务。技术栈为 Python / FastAPI、React / TypeScript、Cytoscape.js 和 SQLite。

![IPtopo 工作区](docs/workbench.png)

## 主要功能

| 功能 | 可以做什么 |
| --- | --- |
| 配置还原 | 批量导入配置文件或 ZIP，提取 IPv4 / IPv6 接口、VRF、LAG / LACP，按网段推断候选连接，查看配置文件与行号证据 |
| 拓扑编辑 | 确认或拒绝关系、手动连线或改接、覆盖字段、添加设备 / 接口 / 备注；自动保存布局、固定节点和过滤条件 |
| IGP 路径仿真 | 手动选择两端设备和 IPv4 端口，基于实际配置的 OSPF / IS-IS 计算最短路径、并行链路和逐跳 ECMP |
| LSDB 分析 | 导入四厂商 OSPFv2 / IS-IS 详细输出，独立还原协议图，或结合配置比较连接、开销和状态差异 |
| AI 对话与 MCP | 使用原生 Codex、专用 OpenCode V1 / V2 服务或支持工具调用的模型 API；通过只读工具查询当前项目，并定位画布与来源 |
| 保存与导出 | SQLite 持久保存项目、不可变快照和人工修正；导出项目 JSON、画布 PNG、配置拓扑 CSV ZIP / draw.io，支持 JSON 恢复 |

全部功能面向 **PC 桌面浏览器**。设备列表、画布和详情 / 对话并列显示，工作区最小宽度为 1280 px；较窄的 PC 窗口可横向滚动。手机和平板专用交互不在当前开发与验收范围。

配置同网段只产生候选关系，不能证明物理直连或在线可达。只有配置输入时，运行状态为 `unknown`；人工确认不代表实时运行状态。LSDB 是采集时的协议观测，ECMP 百分比是理论逐跳分担，均不能替代现场 FIB 或流量验证。

## 快速开始

### Windows 双击启动

1. [下载 Windows x64 便携包](https://github.com/LiuMaoDou/IPtopo/releases/latest/download/IPtopo-Windows-x64.zip)，解压后双击 `IPtopo.exe`。
2. 启动器自动打开浏览器；新建项目，导入配置或使用内置合成示例。
3. 保持启动窗口运行。关闭浏览器不会退出应用；关闭启动窗口或点击“退出”会停止服务。

适用于 Windows 10 / 11 x64，自带前后端和 Python 运行环境，拓扑与路径分析无需另装 Python、Node.js 或 Docker。AI 对话需要单独配置 Agent 或模型服务。数据保存在 `%LOCALAPPDATA%\IPtopo\data`；退出后替换 EXE 即可升级，项目数据保留。构建、校验与 AI 配置见 [Windows 版说明](docs/WINDOWS.md)。

### Docker Compose

```sh
git clone https://github.com/LiuMaoDou/IPtopo.git
cd IPtopo
docker compose up --build -d
```

打开 [工作台](http://localhost:8765)；交互式 API 文档位于 [Swagger UI](http://localhost:8765/docs)。数据保存在 `iptopo-data` Docker 卷。

默认仅绑定本机。需要可信内网访问时设置 `IPTOPO_BIND=0.0.0.0`。应用为单用户工具，没有内置认证和多人权限；公网部署需在反向代理增加访问控制。容器中的 AI CLI、认证与 MCP 回调地址需另行配置，见 [AI 接入说明](docs/AI.md)。

### 从源码启动

使用 Python 3.12+、Node.js 24 和 npm（与 CI / Docker 构建环境一致）。

```sh
git clone https://github.com/LiuMaoDou/IPtopo.git
cd IPtopo
./scripts/dev.sh
```

脚本创建虚拟环境、安装锁定依赖、构建前端并启动 API，默认地址为 [http://127.0.0.1:8765](http://127.0.0.1:8765)。若 `python3` 版本不满足要求：

```sh
IPTOPO_PYTHON=/path/to/python3.12 ./scripts/dev.sh
```

前端开发需要热更新时，完成依赖安装后使用两个终端：

```sh
# 终端 1：在仓库根目录运行 API
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
# 终端 2
cd frontend
npm run dev
```

Vite 将 `/api` 代理到 8765。API 进程必须使用 **一个 Uvicorn worker**；解析任务在独立进程执行，同时最多两个作业。生产构建由同一个 API 服务托管，核心工作台无需 CDN 或外部云服务。

## 使用流程

1. **导入**：新建项目，上传单个 / 多个配置或 ZIP，查看逐文件解析状态、厂商方言、接口和双栈地址。自动识别有误时可选解析器重新导入。
2. **检查证据**：在拓扑画布检查关系、接口端点、前缀与配置行号。默认按规范网段和前缀长度跨厂商、设备及 VRF 归组；需要隔离重复地址空间时手工指定不同地址域。三个以上端点通过网段节点表示。
3. **修正与布局**：确认 / 拒绝关系，手动连线、改接或覆盖字段；拖动与固定节点后自动保存布局。修正独立保存，可撤销或重新启用。
4. **分析**：进入“路径仿真”计算配置路径；进入“LSDB 拓扑”导入观测、查看差异；进入“AI 对话”查询当前项目。
5. **更新**：重新上传同名文件更新设备快照，默认保留其他文件；“替换”模式移除本批次未提供的配置。失败文件不会删除之前的有效配置。
6. **保存与交付**：项目 JSON 包含配置事实快照、修正、视图及 LSDB 批次，可恢复为独立项目；默认不包含原配置，可显式选择包含。PNG 来自当前画布；CSV / draw.io 根据选定的已保存配置视图导出，LSDB 模式中禁用。

可先使用 [八设备配置示例](samples/README.md)、[六设备四厂商 IGP 示例](samples/multi-vendor-igp/README.md) 或 [四厂商 LSDB 示例](samples/multi-vendor-lsdb/README.md)。示例均明确标记为合成数据。

### 配置更新与设备身份

设备身份由项目中持久保存的输入文件映射建立，不按 hostname 或 IP 自动合并；同名设备保留独立身份并报警。仅更换外层 ZIP 包名时，唯一匹配的包内相对路径仍更新原设备；相对路径有歧义时保留独立输入并提示。改动配置文件相对路径、文件内设备名称或厂商时会建立新身份，需复核。若旧项目已有多个同路径设备，可选择“替换快照 · 仅使用本批配置”清理，历史快照保留。更新解析器后需重新导入配置，已有快照不会自动重新解析。

## 厂商与分析范围

### 配置解析与 LAG / LACP

| 厂商 | 配置方言 | 聚合接口 |
| --- | --- | --- |
| Huawei | VRP | Eth-Trunk |
| Cisco | IOS / IOS-XE、IOS-XR、NX-OS | Port-channel / Bundle-Ether |
| Juniper | Junos set / 层级，完整配置、接口子树及带编辑上下文的相对配置 | ae |
| Nokia | SR OS Classic、MD-CLI flat / 层级 | LAG |

支持 IPv4 / IPv6 接口地址与显式配置状态。点击链路或接口可查看聚合接口、成员端口和显式 LACP 参数；LACP、静态聚合与 PAgP 分别标记，物理成员不会重复生成三层链路。缺少配置的状态或协议参数保持未知。

详细字段和边界见 [支持矩阵](docs/SUPPORT_MATRIX.md)。未展开的 Junos groups / interface-range 会产生部分解析诊断；涉及继承时应提供有效配置。运行态 LLDP / CDP、Nokia SR Linux、Batfish 增强、SVG、精细端口走线和多人协作不在本版实现范围。

### OSPF / IS-IS 路径仿真

在配置画布右键设备设为源或目的，或在面板手动选择两端设备，再分别选择 IPv4 端口。源端口提供源地址与本地路由上下文，出口由路由计算；目的可使用普通三层端口或 Loopback。选中后持续显示源 / 目的标记，点击“仿真”才显示路径。协议选项仅来自实际配置，只有多种协议可用时才需要选择；界面不再要求选择实例、Area / Level 或目的 Loopback。

支持四厂商 **IPv4、OSPF 单 Area 和 IS-IS 单 Level 内部路径**。协议范围从两端配置匹配，多个独立范围分别计算，不把分担比例跨组相加。仿真面板不提供场景调整。更换端点会清除旧路径；重新导入配置会清除旧端点与结果。六设备混合示例的三条等价路径理论占比为 **50% / 25% / 25%**。

没有参与本次候选连接的接口诊断不会阻断仿真。Huawei 自动开销支持标称带宽、配置聚合成员求和和子接口继承，并明确标注推演假设；无法确定的双速端口可逐项确认并保存可撤销的带宽修正。保留原文的旧快照自动刷新路由派生字段，详见 [诊断与自动开销](docs/ROUTING.md#诊断范围与自动开销)。

未建模的高级扩展按文档策略忽略并保留警告，基础事实缺失或无效仍限制计算。不支持 IPv6 SPF、跨 Area / Level 选路或真实 FIB Hash 仿真。完整语法、API 与假设见 [路由仿真](docs/ROUTING.md) 和 [厂商默认行为](docs/DEFAULTS.md)。

### LSDB 拓扑与配置融合

在“LSDB 拓扑”导入 Huawei、Cisco、Juniper、Nokia 的 **OSPFv2 / IS-IS 详细 LSDB**。支持空项目独立还原，也可关联配置设备 / 接口，比较开销与连接差异，并计算最短路径和逐跳 ECMP。采集批次与配置快照独立保存，不自动混合不同设备或时刻的观测。

摘要、分页截断、缺少反向声明或其他不完整输入可以查看诊断，但会阻止完整路径计算。支持范围为单 Area / Level 的 Standard IPv4；不支持 OSPFv3 / IPv6、跨域路由或 TE 约束路径。采集命令、完整性规则与示例见 [LSDB 使用说明](docs/LSDB.md)。

### AI 对话与外部 Agent

在“AI 对话”查询设备、接口、逻辑组网及配置 / LSDB 路径，结果卡片可定位画布或来源。支持：

- **Codex（默认）**：后端所在电脑上的原生 CLI，使用独立原生会话与只读工具权限。
- **OpenCode V1 / V2**：连接配置了专用 `iptopo` 只读 Agent 的独立服务，按对应版本设置认证和 API。
- **自定义 API**：支持 OpenAI、DeepSeek 和其他兼容 Responses / Chat Completions 的服务；DeepSeek 预设使用非思考模式，密钥由后端环境变量提供。
- **外部 MCP**：生成绑定当前项目事实上下文的临时只读连接，供外部 Agent 查询。

路径由 IPtopo 的确定性引擎计算。配置片段、提问和历史可能发送到所选模型服务；连接检测不代表模型额度与工具调用已验证。OpenCode V2 已有协议夹具覆盖，尚无真实 V2 服务的模型端到端验收。安装、登录、专用 Agent 配置、容器回调地址和数据边界见 [AI 接入](docs/AI.md) 与 [AI 验证记录](docs/AI_VALIDATION.md)。

## 大规模拓扑与视图

左右侧栏可拖动边界调整宽度，双击边界恢复默认；标题栏两端的侧栏按钮可折叠或展开，展开恢复原来的宽度。宽度与折叠状态在当前浏览器中保留，右侧检查器、仿真、LSDB 和 AI 面板分别记忆宽度。聚焦边界后可用左右方向键微调，调整侧栏不会改变拓扑坐标或缩放。

画布左上角工具栏集中提供选择、手工连线、聚焦相邻、固定、撤销、重做和显示设置。选择设备后可聚焦一跳邻居，再次点击恢复全部；显示设置以浮层展开，标题栏只保留拓扑名称和统计信息。

界面统一使用设备视图，不再提供网段视图切换。左下角“回到中心”可一键居中并适配整个拓扑，保持设备坐标不变。设备使用厂商矢量图标，链路状态有独立图例。两台设备之间的多条三层链路默认合并为粗线并显示数量；单击查看成员，双击展开，再次双击成员链路可收起。各链路的接口、地址和审阅状态独立保存，网段成员关系不聚合。

“显示设置 → 链路 Cost”可显示或隐藏配置中的 IPv4 IGP 开销，显示设置自动保存；纯 IPv6 关系不套用 IPv4 开销。双向开销相同时居中显示，不同时分别标在出口设备一侧；聚合链路汇总已使能协议的成员开销，超过三个不同值时显示范围。没有协议绑定的接口不补默认值，已绑定但开销未知时显示“待确认”。

展开平行链路时，曲线按标签高度拉开间距，网段与 Cost 使用独立底色标签；缩小到紧凑视图时仍保留这些标签。纯 IPv6 成员标注“IPv6 · Cost 未解析”，以区分尚未解析的开销与可用的 IPv4 开销。

“链路地址”开启时，展开的链路在对应设备一侧显示接口名和本端实际 IP；选中单条链路也可查看两端地址。同一接口的 IPv4、IPv6 分行标识，只显示该链路关联的网段中的地址。未选中的普通链路以“网段”标记网络前缀，多链路汇总按设备分别列出接口与地址。

大图首次打开采用快速连通分量摆放；重新导入只安排新增节点，保留已有坐标和固定状态。缩小时简化图标、文字及连线细节，放大或选中后恢复。可通过设备/接口/IP 搜索、厂商/地址族/地址域筛选及一跳邻居聚焦查看局部，并把局部视图保存、导出。

复杂布局在可取消的 Web Worker 中运行，设备列表按可见范围渲染。100 / 1,000 / 2,000 台连通网络的实测、阈值及复现步骤见 [性能报告](docs/PERFORMANCE.md)。

## 数据、限制与备份

| 项目 | 默认值 / 位置 |
| --- | --- |
| 源码运行数据 | 仓库根目录 `data/` |
| Windows EXE 数据 | `%LOCALAPPDATA%\IPtopo\data` |
| Docker 数据 | `iptopo-data` 命名卷，容器内 `/data` |
| 数据目录覆盖 | `IPTOPO_DATA_DIR` |
| 配置上传 | 最多 2,500 个文件，单文件 4 MiB，累计实际展开 64 MiB，ZIP 嵌套 3 层 |
| 上传限额变量 | `IPTOPO_MAX_FILES`、`IPTOPO_MAX_FILE_BYTES`、`IPTOPO_MAX_TOTAL_BYTES`、`IPTOPO_MAX_ZIP_DEPTH` |
| 项目 JSON 导出 / 恢复 | 独立的 256 MiB 限额，通过 `IPTOPO_MAX_RESTORE_BYTES` 调整 |

`iptopo.sqlite` 保存项目、快照、作业和聊天；`sources/` 保存解码后的原配置文本，`uploads/` 保存作业临时文件。ZIP 递归读取，不按用户路径解压到磁盘；拒绝越界路径、符号链接、加密或不支持的归档。二进制与元数据跳过并报告，嵌套归档的实际展开数据也计入限额。

单文件错误独立报告。取消作业或重启中断不会提交半个快照；重启后未完成作业标记为 `interrupted`，可以重新上传。更新请求带项目版本，冲突返回 409；导入期间暂停其他项目写入。

- **便携项目备份**：导出 JSON；需要原文时勾选包含原配置。项目 JSON 不包含聊天、AI 连接偏好或原生 Agent 历史。
- **完整服务备份**：先停止服务，再复制整个数据目录 / 数据卷，包括数据库与 `sources/`。排除 `ai/codex-home/auth.json`，不要跟随该认证符号链接；硬链接备份也需排除此文件。使用同版本或兼容 schema 恢复后，重新建立原生 Codex 登录。

日志不输出配置正文，前端按纯文本显示配置。配置文件可能含凭据，请将数据目录和含原文的备份放在受控位置。更多说明见 [AI 数据边界](docs/AI.md#本地数据与排错)。

## 开发与验证

在已通过启动脚本建立依赖环境的仓库中运行：

```sh
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m scripts.validate_public_network
cd frontend
npm ci
npm test
npm run build
```

`requirements.lock` 和 `frontend/package-lock.json` 锁定完整依赖。Linux CI 使用 Python 3.12 / Node.js 24，执行后端回归、公开厂商样本验证、前端测试与构建；Windows 工作流另执行 PyInstaller 打包和实际 EXE 启动检查。

验证包含合成回归样本与 [38 份四厂商官方文档样本](samples/public-network/README.md)，不代表真实设备全版本兼容性认证或完整现场 FIB 验收。范围与证据见 [验收记录](docs/VALIDATION.md)、[公开样本验证](docs/PUBLIC_NETWORK_VALIDATION.md) 和 [AI 验证记录](docs/AI_VALIDATION.md)。

合成规模测试（从仓库根目录执行）：

```sh
.venv/bin/python scripts/benchmark.py
.venv/bin/python scripts/generate_scale_samples.py --devices 1000 --output /tmp/iptopo-scale-1000.zip
.venv/bin/python scripts/benchmark_scale_api.py --devices 100 1000 2000
```

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [Windows 版](docs/WINDOWS.md) | 便携包、源码构建、数据迁移与 AI 配置 |
| [支持矩阵](docs/SUPPORT_MATRIX.md) | 配置方言、字段、LAG / LACP、继承与解析边界 |
| [路由仿真](docs/ROUTING.md) · [默认行为](docs/DEFAULTS.md) | OSPF / IS-IS、场景假设、厂商默认参数与 FIB 差异 |
| [LSDB 分析](docs/LSDB.md) | 采集命令、批次、配置融合、SPF / ECMP 与完整性检查 |
| [AI 接入](docs/AI.md) · [AI 验证](docs/AI_VALIDATION.md) | Codex、OpenCode、模型 API、MCP、认证与数据边界 |
| [验收记录](docs/VALIDATION.md) · [公开样本验证](docs/PUBLIC_NETWORK_VALIDATION.md) | 回归范围、来源证据与尚未验证的边界 |
| [性能报告](docs/PERFORMANCE.md) | 规模测试、浏览器表现与复现命令 |
| [数据契约](docs/CONTRACT.md) · [设计说明](docs/DESIGN.md) | 模型、API、推断与持久化设计 |
| [依赖与许可证](docs/DEPENDENCIES.md) | 第三方组件来源和许可证 |

## 目录

```text
backend/app/     API、解析、推断、路由、LSDB、AI、持久化与导出
backend/tests/   解析、拓扑、路径、LSDB、AI 与持久化回归
frontend/        React 工作区、Cytoscape、布局 Worker 与对话面板
samples/         合成配置、IGP / LSDB 示例与官方文档样本
docs/            数据契约、支持矩阵、设计与验证
scripts/         本机启动、公开样本验证与合成基准
packaging/       Windows EXE 构建配置
desktop.py       桌面启动器
```

原开发计划保留在 [IP_Topology_App_Plan.md](IP_Topology_App_Plan.md)。

## 许可证

项目沿用仓库原有的 [GPL-3.0 许可证](LICENSE)。第三方组件说明见 [依赖与许可证](docs/DEPENDENCIES.md)。
