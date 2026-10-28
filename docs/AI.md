# AI 对话与 Agent 接入

AI 对话与其他功能统一面向 PC 桌面浏览器，设备列表、拓扑画布和对话面板并列使用。手机和平板专用界面不在开发与验收范围。

打开项目，在工具栏进入 **AI 对话**。可以查询设备、接口、地址、逻辑组网，以及配置或 LSDB 路径。例如：“当前有多少台设备？”、“IGP-A 和谁相连？”、“列出可选的 OSPF 路径起点和终点，再计算路径”。选中设备、网段或链路后，提问会携带当前选择。

模型通过 IPtopo 的只读工具取得事实，路径使用现有 SPF 引擎计算。回答下方的设备、组网、路径和证据卡片可以定位到画布或来源。缺少参数时应先列候选；配置同网段不能证明物理直连，LSDB 观测也不能证明此刻可达，ECMP 百分比是理论逐跳分担。

## 选择连接方式

| 后端 | 运行位置 | 认证 | 本项目的连接方式 |
| --- | --- | --- | --- |
| Codex（默认） | IPtopo 后端所在电脑 | 原生 Codex CLI 登录 | `app-server` stdio JSON-RPC、原生 thread/turn、IPtopo MCP |
| OpenCode V1 | 已启动的专用 OpenCode 服务 | 服务口令 + OpenCode 自己的模型认证 | V1 HTTP API、事件流、原生 session/abort |
| OpenCode V2 | 已启动的专用 OpenCode V2 服务 | 服务口令 + OpenCode 自己的模型认证 | 独立 V2 HTTP API、事件与投影消息、原生 interrupt |
| 模型 API | 配置的 HTTP(S) 服务 | IPtopo 后端的环境变量 | Responses 或 Chat Completions 流式工具调用 |

在“AI Agent 设置”保存地址、模型和环境变量**名称**，然后“保存并检测”。已有会话保留原来的后端和模型；更换后新建会话。检测不发模型推理：Codex 检查协议、隔离配置与登录，OpenCode 检查服务和专用 Agent，API 检查模型目录可达性。模型权限、额度和实际工具兼容性需要第一轮对话才能确认。

配置与来源片段、提问和历史可能发送到所选模型服务；模型服务由使用者选择。应用不要求在网页填写 API Key，也不将 API Key 写入项目 JSON。

## Codex 原生接入

在**运行 IPtopo 后端的用户和环境**中安装并登录 [Codex CLI](https://learn.chatgpt.com/docs/cli)。先确认：

```sh
codex --version
codex login
```

设置中选择 Codex；程序路径填写 `codex` 或绝对路径，模型可留空使用原生默认值。Docker / Windows EXE 内部运行的后端也必须能访问对应 CLI 和登录状态；宿主机安装不会自动出现在容器中。

Windows 下，npm 安装通常在 PATH 提供 `codex.cmd`。IPtopo 会在相邻的官方 `@openai/codex` 安装中查找原生 `codex.exe`，不经 `cmd.exe` 执行脚本。0.163.0 的 x64 npm 原生程序通常位于 `<npm-prefix>\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`，旧版本目录也可能使用 `codex\codex.exe`。如果安装布局不能自动识别，在设置里填写实际 `codex.exe` 的完整路径，不加引号或命令参数。请保留其安装目录中的相邻组件；IPtopo Windows 包本身不捆绑 Codex。独立安装说明见 [Windows 版 AI 接入](WINDOWS.md#ai-对话)。

IPtopo 为原生会话建立持久专用目录 `<IPTOPO_DATA_DIR>/ai/codex-home`，不导入用户全局 AGENTS、Skills、记忆、MCP、Hooks、插件和历史。仅保留有限模型连接参数，并由 CLI 复用原生 `auth.json` 登录：优先符号链接；系统不允许符号链接时尝试硬链接，硬链接会在启动时刷新。应用不打开或复制认证文件内容。若系统不允许这两种链接，检测会明确提示。

如果使用系统钥匙串、链接不可用或希望单独登录，可直接对这个专用目录执行原生登录。macOS / Linux 使用一次性环境变量，不影响之后启动 IPtopo 的环境：

```sh
CODEX_HOME="/absolute/path/to/data/ai/codex-home" codex login
```

Windows PowerShell（EXE 默认数据位置）可使用：

```powershell
$previousCodexHome = $env:CODEX_HOME
try {
  $env:CODEX_HOME = Join-Path $env:LOCALAPPDATA "IPtopo\data\ai\codex-home"
  codex login
} finally {
  $env:CODEX_HOME = $previousCodexHome
}
```

连接创建时，IPtopo 检查生效配置，并在启动模型前核验本轮 MCP 已连接、工具非空且声明只读。Shell、统一执行器、文件修改、浏览器、Web Search、其他 MCP、子代理与 Hooks 等关闭；Agent 请求提升权限或调用其他工具时拒绝。原生线程可跨轮恢复；项目事实、选择的 LSDB 批次、画布模式或连接配置改变后，应用建立新的原生上下文并保留可见对话历史。

当前实测 CLI 为 **0.163.0**。该版本必须保留 `features.code_mode_host=true` 才能分发 MCP 调用，同时仍设置 `features.code_mode=false` 禁用脚本工具；关闭宿主分发会造成“工具目录已连接、模型无法调用”。旧版或受管配置无法确认隔离时会阻断，不能用放宽权限来绕过检测。原生协议与配置参考：[App Server](https://learn.chatgpt.com/docs/app-server)、[配置字段](https://learn.chatgpt.com/docs/config-file/config-reference)、[AGENTS 发现规则](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。

## OpenCode V1 专用服务

IPtopo 连接现有服务，不接管或修改用户的全局 OpenCode 配置。必须有名为 `iptopo` 的自定义主 Agent；默认拒绝所有工具，只允许 `iptopo_*` MCP。普通 `build` / `plan` 不满足这个要求。

建议单独目录和端口。macOS / Linux 可先建立目录：

```sh
IPTOPO_AGENT_DIR="$HOME/.local/share/iptopo-opencode-v1"
mkdir -p "$IPTOPO_AGENT_DIR/config/opencode" "$IPTOPO_AGENT_DIR/data" \
  "$IPTOPO_AGENT_DIR/cache" "$IPTOPO_AGENT_DIR/state" "$IPTOPO_AGENT_DIR/work"
chmod 700 "$IPTOPO_AGENT_DIR"
```

将下面完整 JSON 保存为 `$IPTOPO_AGENT_DIR/config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "permission": {"*": "deny"},
  "agent": {
    "iptopo": {
      "description": "IPtopo read-only network assistant",
      "mode": "primary",
      "prompt": "只使用当前会话的 IPtopo MCP 查询网络事实，用中文回答。禁止 Shell、文件读写、外部搜索、其他 MCP 和子代理。",
      "permission": {"*": "deny", "iptopo_*": "allow"}
    }
  },
  "plugin": []
}
```

在专用终端使用该环境完成模型登录并启动服务：

```sh
export XDG_CONFIG_HOME="$IPTOPO_AGENT_DIR/config"
export XDG_DATA_HOME="$IPTOPO_AGENT_DIR/data"
export XDG_CACHE_HOME="$IPTOPO_AGENT_DIR/cache"
export XDG_STATE_HOME="$IPTOPO_AGENT_DIR/state"
export OPENCODE_DISABLE_PROJECT_CONFIG=true
export OPENCODE_DISABLE_CLAUDE_CODE=true
export OPENCODE_DISABLE_DEFAULT_PLUGINS=true
export OPENCODE_SERVER_USERNAME=opencode
cd "$IPTOPO_AGENT_DIR/work"
opencode auth login
opencode serve --hostname 127.0.0.1 --port 4096
```

通过环境管理工具给服务设置 `OPENCODE_SERVER_PASSWORD`，同时让 IPtopo 后端进程获得相同值。不要把口令写进上述 JSON。在 IPtopo 选择 OpenCode、API `v1`、地址 `http://127.0.0.1:4096`，口令环境变量名保持 `OPENCODE_SERVER_PASSWORD`。本适配器使用用户名 `opencode`；模型若填写，格式为 `provider/model`，留空使用服务配置。

实测 **OpenCode 1.19.5** 的 `/global/health`、会话权限回显、远程 MCP `initialize → tools/list`、原生 `/abort` 和 MCP 断开接口。V1 的 API 路径不带 `/api` 前缀；完整模型问答仍取决于服务中的模型认证。参考：[Server / 认证 / OpenAPI](https://opencode.ai/docs/server/)、[Agent](https://opencode.ai/docs/agents/)、[权限](https://opencode.ai/docs/permissions/)。

## OpenCode V2 专用服务

V2 的 API、配置字段和环境变量与 V1 不同，不能只修改服务 URL。在新终端准备另一独立目录，使用 V2 CLI：

```sh
IPTOPO_AGENT_DIR="$HOME/.local/share/iptopo-opencode-v2"
mkdir -p "$IPTOPO_AGENT_DIR/config/opencode" "$IPTOPO_AGENT_DIR/data" \
  "$IPTOPO_AGENT_DIR/cache" "$IPTOPO_AGENT_DIR/state" "$IPTOPO_AGENT_DIR/work"
chmod 700 "$IPTOPO_AGENT_DIR"
export XDG_CONFIG_HOME="$IPTOPO_AGENT_DIR/config"
export XDG_DATA_HOME="$IPTOPO_AGENT_DIR/data"
export XDG_CACHE_HOME="$IPTOPO_AGENT_DIR/cache"
export XDG_STATE_HOME="$IPTOPO_AGENT_DIR/state"
cd "$IPTOPO_AGENT_DIR/work"
```

将以下完整 JSON 放在这个专用工作目录的 `opencode.jsonc` 中：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "snapshots": false,
  "permissions": [{"action": "*", "resource": "*", "effect": "deny"}],
  "agents": {
    "iptopo": {
      "description": "IPtopo read-only network assistant",
      "mode": "primary",
      "system": "只使用当前会话的 IPtopo MCP 查询网络事实，用中文回答。禁止 Shell、文件读写、外部搜索、其他 MCP 和子代理。",
      "permissions": [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "iptopo_*", "resource": "*", "effect": "allow"}
      ]
    }
  },
  "plugins": [],
  "mcp": {"servers": {}}
}
```

为这个独立服务设置专用 XDG 配置 / 数据 / 缓存 / 状态目录，在空的专用工作目录中启动，避免加载其他项目规则。模型认证通过 V2 自身管理；例如按提供商文档使用 `opencode auth login <provider>`。V2 的前台独立服务器命令为：

```sh
opencode serve --hostname 127.0.0.1 --port 4097
```

V2 通过 **`OPENCODE_PASSWORD`** 设置固定服务口令，HTTP 客户端仍使用 Basic Auth、用户名 `opencode`。让 IPtopo 进程读取相同的环境变量，设置中选择 API `v2`、URL `http://127.0.0.1:4097`，并把口令环境变量名改为 **`OPENCODE_PASSWORD`**。不要把 V1 的 `OPENCODE_SERVER_PASSWORD` 默认值当作 V2 服务的原生环境变量。

| 项目 | V1 | V2 |
| --- | --- | --- |
| Agent 配置 | `agent.iptopo.permission` | `agents.iptopo.permissions` |
| 规则字段 | `permission / pattern / action` | `action / resource / effect` |
| 会话创建 | `/session` | `/api/session` |
| 提问 | `/session/{id}/message` | `/api/session/{id}/prompt` |
| 停止 | `/session/{id}/abort` | `/api/session/{id}/interrupt` |
| 远程 MCP | `POST /mcp` | `PUT /api/experimental/mcp/{server}` |
| 模型参数 | 提问使用 `{providerID, modelID}` | 创建 / 切换会话使用 `{providerID, model}` |

IPtopo 在每个会话中再次设置“全部拒绝，仅当前随机 MCP 前缀允许”的权限，并验证服务回显。V2 事件是实时且不保证重放，因此同时读取消息投影确认增量和最终完成状态；不重发提问。运行时 MCP 接口属于 V2 的 experimental API，版本不兼容或权限不能验证时会明确失败。

V2 目前依据官方契约实现并经过本地 HTTP 协议夹具测试，尚未进行真实 V2 服务的模型端到端验证；V1 实测不能替代 V2 实测。启动和口令依据 [V2 独立服务](https://opencode.ai/v2/docs/cli/web)、[V2 CLI 命令](https://opencode.ai/v2/docs/cli/commands)，配置依据 [Agent](https://opencode.ai/v2/docs/agents)、[权限](https://opencode.ai/v2/docs/permissions)、[原生 API](https://opencode.ai/v2/docs/api)。

## 模型 API

选择“模型 API”，填写模型服务支持的模型 ID、基础地址和 API Key 环境变量名。默认基础地址为 `https://api.openai.com/v1`，默认环境变量名为 `OPENAI_API_KEY`。密钥必须位于 **IPtopo 后端进程**的环境中；终端、服务管理器或容器的环境不会自动互相继承。修改凭据环境后重启对应后端进程。

- `auto`：官方 `api.openai.com` 使用 Responses，其他地址使用 Chat Completions。
- `responses`：显式调用 `/responses`，发送 `store=false`，保留工具续轮需要的加密 reasoning 项；reasoning 不显示到界面。
- `chat_completions`：显式调用 `/chat/completions`，累积流式函数参数，执行查询后把结果送回模型续答。

服务必须支持流式函数工具调用。模型参数经过 JSON 解析，工具名称必须在注册目录中；业务执行器还会验证字段、数量、项目归属、实体 ID 和上下文。每轮最多 16 次工具调用、8 个模型步骤，回答最多 100,000 字符，默认单轮超时 120 秒（可设 15–600 秒）。目录检测通过只表示服务可达，不证明工具调用可用。协议参考：[Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)、[Function calling](https://developers.openai.com/api/docs/guides/function-calling)。

## 外部 Codex / OpenCode 的 MCP 连接

在设置中展开“连接外部 Codex / OpenCode”，生成临时连接配置。连接范围仅限生成时的项目事实上下文，支持设备搜索、设备详情、网络摘要、逻辑组网、路径目标、配置路径、LSDB 路径、对比和脱敏证据九个工具。项目内容更新后重新生成连接，以取得新事实。

生成的配置用 `IPTOPO_MCP_TOKEN` 环境变量引用 Bearer 令牌；把令牌单独设置给外部 Agent，不要提交到版本库。有效期默认 30 分钟，可选 1–1440 分钟；IPtopo 重启后令牌失效。应用内原生会话自动分配短期令牌，并在本轮结束时撤销。外部 Agent 自身的其他工具权限仍由其配置控制；IPtopo 的 MCP 入口只提供当前项目的只读查询。

MCP 地址默认由后端监听端口产生 `http://127.0.0.1:<port>/api/ai/mcp`。如果 Agent 和 IPtopo 位于不同容器或电脑，在 **IPtopo 后端启动环境**配置 `IPTOPO_AI_CALLBACK_URL` 为 Agent 能访问的基础 URL 或完整 MCP URL，例如：

```sh
IPTOPO_AI_CALLBACK_URL=http://iptopo:8765 ./scripts/dev.sh
```

该地址必须从 Agent 所在环境实际可达；浏览器能打开 IPtopo 不能替代这个检查。应用不会使用任意请求 Host 头决定向哪里发送 Agent 令牌。可参考 [MCP 官方传输规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

## 本地数据与排错

聊天会话、回答、结果卡片和引用记录保存在 IPtopo 的 SQLite 中；连接偏好保存在 `data/ai/settings.json`，Codex 原生会话保存在 `data/ai/codex-home`。它们均不进入便携项目 JSON。删除会话会删除对应的 IPtopo 本地聊天记录；原生 Agent 自己保存的会话由对应 Agent 管理。项目 JSON 导入后建立新的本地对话，不复用旧 Agent 线程。

完整服务目录备份时，排除 `ai/codex-home/auth.json`，不要跟随该认证符号链接；硬链接在文件备份中也可能表现为普通认证文件。恢复后使用原生登录重新建立认证。连接设置只保存环境变量名，不备份这些变量的密钥值。

常见情况：

- **找不到 CLI / 未登录**：在后端实际运行的用户和环境中检查程序路径，使用上述专用目录登录方法。
- **OpenCode 需要专用 Agent**：按对应 V1/V2 配置启动独立服务；不要把普通 `plan` 的名称当成只读权限保证。
- **MCP 未连接 / 401**：检查回调地址、令牌到期时间和服务是否重启；外部连接重新生成令牌。
- **模型目录可达但对话失败**：检查模型 ID、协议选择、额度和工具调用支持；检测不会自动切换模型或降级为无工具猜测。
- **停止 / 超时**：界面停止会调用 Codex `turn/interrupt` 或 OpenCode 原生 abort/interrupt；不会仅丢弃流式文字。已收到的部分回答可保留并标记取消或错误。
- **问题无法唯一定位**：说明候选设备名称、VRF / 协议范围、Area/Level 或 LSDB 批次后继续提问。工具返回 `incomplete`、`unsupported`、`unreachable` 时保留其原意。

