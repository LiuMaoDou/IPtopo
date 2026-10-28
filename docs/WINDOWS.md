# IPtopo Windows 版

[下载 Windows x64 便携包](https://github.com/LiuMaoDou/IPtopo/releases/latest/download/IPtopo-Windows-x64.zip) · [GitHub Releases](https://github.com/LiuMaoDou/IPtopo/releases)

面向 Windows 10/11 x64。拓扑与路径分析无需安装 Python、Node.js 或 Docker；可选 AI 对话需要另行配置 Codex、OpenCode 服务或模型 API。

1. 解压下载包，双击 `IPtopo.exe`。首次启动需要几秒钟解包。
2. 启动窗口出现后会自动打开默认浏览器；也可以点击“打开工作台”。
3. 新建项目 → 导入配置文件或 ZIP → 查看拓扑 → 调整布局（自动保存）或导出。左右侧栏可调宽、折叠，画布工具栏可聚焦相邻设备及调整显示设置。
4. 保持启动窗口运行。关闭浏览器不会退出应用；关闭启动窗口或点击“退出”会停止服务。

数据默认保存在 `%LOCALAPPDATA%\IPtopo\data`，不放在 EXE 的临时解包目录。更新程序时先退出，再替换 EXE；数据保留。程序仅监听本机，自动选择空闲端口。重复启动不会同时操作同一个数据库。

Mac 上原有项目不会自动进入 Windows 包。请在 Mac 工作台导出项目 JSON，然后在 Windows 工作台点击“恢复项目”。需要配置原文时，在导出时勾选包含原配置。

备份：退出程序后复制数据目录，排除 `ai\codex-home\auth.json`，不要跟随该认证符号链接；硬链接备份也应排除此文件。也可在工作台导出项目 JSON，其不包含聊天、AI 连接偏好或原生 Agent 登录。项目 JSON 导出 / 恢复默认使用独立的 256 MiB 限额，可通过 `IPTOPO_MAX_RESTORE_BYTES` 调整。启动失败时查看数据目录中的 `desktop.log`。

本构建未进行 Windows 代码签名，下载运行时可能出现发布者提示。支持范围以实际 Windows 构建测试为准；不包含 ARM64 原生版本。

## AI 对话

打开项目后，工具栏进入“AI 对话”，在设置中选择连接方式。Windows EXE 不包含 Codex 或 OpenCode，也不附带任何模型凭据。

- **Codex**：按 [官方 CLI 安装说明](https://learn.chatgpt.com/docs/cli) 安装并使用同一个 Windows 用户完成 `codex login`。程序路径可填 `codex`，也可直接选择原生 `codex.exe`。npm 的 `codex.cmd` 启动器会被解析为相邻的官方原生 EXE，不会交给命令 Shell 执行。安装后需要退出并重新打开 IPtopo，才能读取新 PATH。
- **OpenCode**：连接单独启动、已配置专用 `iptopo` 只读 Agent 的服务，选择对应 V1/V2 API；服务配置和口令环境变量存在版本差异，详见 [AI 接入说明](AI.md)。
- **自定义 API**：支持 OpenAI、DeepSeek 和其他兼容 Responses / Chat Completions 的服务。选择预设或填写模型、基础 URL、协议和凭据环境变量名；DeepSeek 预设使用非思考模式。环境变量必须由 `IPtopo.exe` 进程继承；修改系统环境后重启应用。

以 Codex 0.163.0 的默认 npm x64 安装为例，原生程序通常位于：

```text
%APPDATA%\npm\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe
```

实际位置随 npm prefix 和版本变化；保留原安装树中的相邻组件，填写路径时不加引号和命令参数。自动定位失败或程序架构不兼容时，设置检测会给出原生 EXE 路径提示。

Codex 会话放在 `%LOCALAPPDATA%\IPtopo\data\ai\codex-home`（设置 `IPTOPO_DATA_DIR` 时使用相应目录）。优先用符号链接复用原生登录；无符号链接权限时尝试同磁盘硬链接。跨盘且没有符号链接权限时会明确失败，可按 [专用目录登录方法](AI.md#codex-原生接入) 单独登录；应用不会通过复制凭据来绕过限制。

桌面启动器使用支持异步子进程的 Windows 事件循环。冻结环境启动外部 CLI 时临时恢复系统 DLL 搜索路径，完成创建后还原应用路径；不会为运行 AI 再启动一个 IPtopo 实例。相关打包行为见 [PyInstaller 外部程序说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)。

## 从源码构建

在 Windows x64 上安装 Python 3.12 和 Node.js 24，进入仓库目录：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.lock -r packaging/requirements.txt
cd frontend
npm ci
npm run build
cd ..
.venv\Scripts\python -m PyInstaller --clean --noconfirm packaging/windows.spec
.venv\Scripts\python scripts/smoke_desktop.py --exe dist/IPtopo.exe
```

输出为 `dist\IPtopo.exe`。也可以在 GitHub Actions 的 **Windows executable** 工作流中选择 **Run workflow**，成功后下载 **IPtopo-Windows-x64** artifact。自动测试实际 EXE 的窗口、静态资源、多进程解析、导出、重复启动保护、退出和重启后的数据保留；测试时 PATH 不包含 Python 或 Node。

仓库：[LiuMaoDou/IPtopo](https://github.com/LiuMaoDou/IPtopo)。随包附带对应版本源码和 GPL-3.0 许可证，第三方组件说明见源码的 `docs/DEPENDENCIES.md`。

构建工具：[PyInstaller](https://pyinstaller.org/)，仅用于构建；Windows EXE 必须在 Windows 环境构建。
