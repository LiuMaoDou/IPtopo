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
| 拓扑编辑 | 确认或拒绝关系、手动连线或改接、覆盖字段、添加设备 / 接口 / 备注；保存布局、固定节点、过滤条件和命名视图 |
| IGP 路径仿真 | 基于四厂商配置计算 OSPF / IS-IS 最短路径、并行链路和逐跳 ECMP；临时调整接口开销或模拟接口退出 |
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
2. **检查证据**：在设备 / 网段视图检查关系、接口端点、前缀与配置行号。默认按规范网段和前缀长度跨厂商、设备及 VRF 归组；需要隔离重复地址空间时手工指定不同地址域。三个以上端点通过网段节点表示。
3. **修正与布局**：确认 / 拒绝关系，手动连线、改接或覆盖字段；拖动、固定节点并保存命名视图。修正独立保存，可撤销或重新启用。
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

在配置画布右键设备，设为仿真源或目的，再选择协议实例、已发布的 /32 Loopback 和 Area / Level；唯一候选自动确定，多个候选需明确选择。支持四厂商 **IPv4、OSPF 单 Area 和 IS-IS 单 Level 内部路径**，显示最短路径、并行链路及逐跳 ECMP 理论分担。

场景可临时修改开销、补充未知开销假设或模拟接口退出，原始配置不变；重新导入配置会清除旧端点、临时场景、结果和高亮。六设备混合示例的三条等价路径理论占比为 **50% / 25% / 25%**。

未建模的高级扩展按文档策略忽略并保留警告，基础事实缺失或无效仍限制计算。不支持 IPv6 SPF、跨 Area / Level 选路或真实 FIB Hash 仿真。完整语法、API 与假设见 [路由仿真](docs/ROUTING.md) 和 [厂商默认行为](docs/DEFAULTS.md)。

### LSDB 拓扑与配置融合

在“LSDB 拓扑”导入 Huawei、Cisco、Juniper、Nokia 的 **OSPFv2 / IS-IS 详细 LSDB**。支持空项目独立还原，也可关联配置设备 / 接口，比较开销与连接差异，并计算最短路径和逐跳 ECMP。采集批次与配置快照独立保存，不自动混合不同设备或时刻的观测。

摘要、分页截断、缺少反向声明或其他不完整输入可以查看诊断，但会阻止完整路径计算。支持范围为单 Area / Level 的 Standard IPv4；不支持 OSPFv3 / IPv6、跨域路由或 TE 约束路径。采集命令、完整性规则与示例见 [LSDB 使用说明](docs/LSDB.md)。

### AI 对话与外部 Agent

在“AI 对话”查询设备、接口、逻辑组网及配置 / LSDB 路径，结果卡片可定位画布或来源。支持：

- **Codex（默认）**：后端所在电脑上的原生 CLI，使用独立原生会话与只读工具权限。
- **OpenCode V1 / V2**：连接配置了专用 `iptopo` 只读 Agent 的独立服务，按对应版本设置认证和 API。
- **模型 API**：支持流式工具调用的 Responses / Chat Completions 服务；密钥由后端环境变量提供。
- **外部 MCP**：生成绑定当前项目事实上下文的临时只读连接，供外部 Agent 查询。

路径由 IPtopo 的确定性引擎计算。配置片段、提问和历史可能发送到所选模型服务；连接检测不代表模型额度与工具调用已验证。OpenCode V2 已有协议夹具覆盖，尚无真实 V2 服务的模型端到端验收。安装、登录、专用 Agent 配置、容器回调地址和数据边界见 [AI 接入](docs/AI.md) 与 [AI 验证记录](docs/AI_VALIDATION.md)。

## 大规模拓扑与视图

设备使用厂商矢量图标，链路状态有独立图例。两台设备之间的多条三层链路默认合并为粗线并显示数量；单击查看成员，双击展开，再次双击成员链路可收起。各链路的接口、地址和审阅状态独立保存，网段成员关系不聚合。

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
