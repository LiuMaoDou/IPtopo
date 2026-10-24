# IPtopo Windows 版

面向 Windows 10/11 x64。无需安装 Python、Node.js 或 Docker。

1. 解压下载包，双击 `IPtopo.exe`。首次启动需要几秒钟解包。
2. 启动窗口出现后会自动打开默认浏览器；也可以点击“打开工作台”。
3. 新建项目 → 导入配置文件或 ZIP → 查看设备/网段 → 调整布局 → 保存或导出。
4. 保持启动窗口运行。关闭浏览器不会退出应用；关闭启动窗口或点击“退出”会停止服务。

数据默认保存在 `%LOCALAPPDATA%\IPtopo\data`，不放在 EXE 的临时解包目录。更新程序时先退出，再替换 EXE；数据保留。程序仅监听本机，自动选择空闲端口。重复启动不会同时操作同一个数据库。

Mac 上原有项目不会自动进入 Windows 包。请在 Mac 工作台导出项目 JSON，然后在 Windows 工作台点击“恢复项目”。需要配置原文时，在导出时勾选包含原配置。

备份：退出程序后复制整个数据目录；或者在工作台导出项目 JSON。启动失败时查看数据目录中的 `desktop.log`。

本构建未进行 Windows 代码签名，下载运行时可能出现发布者提示。支持范围以实际 Windows 构建测试为准；不包含 ARM64 原生版本。

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
