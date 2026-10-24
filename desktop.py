"""Standalone launcher. Keep freeze_support before imports that can spawn work."""
import multiprocessing
import os
import sys

# The windowed bootloader has no console, including in parser subprocesses.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

if __name__ == "__main__":
    multiprocessing.freeze_support()

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import threading
import time
import webbrowser


def default_data_dir():
    if os.environ.get("IPTOPO_DATA_DIR"):
        return Path(os.environ["IPTOPO_DATA_DIR"]).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "IPtopo" / "data"
    return Path.home() / "Library/Application Support/IPtopo/data"


class InstanceLock:
    """OS lock survives crashes without leaving a stale ownership marker."""
    def __init__(self, folder):
        self.file = (folder / "desktop.lock").open("a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise RuntimeError("IPtopo 已在运行，请使用已有的启动窗口。") from error

    def close(self):
        self.file.close()


def main():
    parser = argparse.ArgumentParser(description="IPtopo desktop launcher")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--headless", action="store_true", help="Run without the launcher window")
    parser.add_argument("--ready-file", type=Path, help="Write startup information for automation")
    parser.add_argument("--stop-file", type=Path, help="Stop when this file exists")
    args = parser.parse_args()
    folder = args.data_dir.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # Set before importing backend.app.main, including in spawned parser workers.
    os.environ["IPTOPO_DATA_DIR"] = str(folder)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    handler = RotatingFileHandler(folder / "desktop.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    lock = None
    server = None
    thread = None
    sock = None
    window = None
    try:
        lock = InstanceLock(folder)
        from backend.app.main import app
        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Reserve an available loopback port; never expose the desktop app to LAN.
        sock.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/"
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, loop="asyncio", http="h11", ws="none"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 60
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError(f"服务启动失败，请查看 {folder / 'desktop.log'}")
            time.sleep(0.05)

        def stopping():
            return not thread.is_alive() or (args.stop_file and args.stop_file.exists())

        def open_app():
            webbrowser.open(url)

        if not args.headless:
            import tkinter as tk
            from tkinter import ttk
            window = tk.Tk()
            window.title("IPtopo")
            window.geometry("520x245")
            window.minsize(460, 245)
            frame = ttk.Frame(window, padding=24)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="IPtopo 已启动", font=("Segoe UI", 18, "bold")).pack(anchor="w")
            ttk.Label(frame, text="在浏览器中导入配置、查看和编辑拓扑。\n保持此窗口运行，关闭窗口将退出应用。", padding=(0, 12)).pack(anchor="w")
            ttk.Label(frame, text=url).pack(anchor="w")
            buttons = ttk.Frame(frame, padding=(0, 18, 0, 0))
            buttons.pack(anchor="w")
            ttk.Button(buttons, text="打开工作台", command=open_app).pack(side="left", padx=(0, 12))
            ttk.Button(buttons, text="退出", command=window.destroy).pack(side="left")
            window.update()

            def tick():
                if stopping():
                    window.destroy()
                else:
                    window.after(250, tick)
            window.after(250, tick)

        if args.ready_file:
            args.ready_file.write_text(json.dumps({"url": url, "pid": os.getpid(), "data_dir": str(folder)}, ensure_ascii=False), encoding="utf-8")
        if not args.no_browser:
            open_app()
        if window:
            window.mainloop()
        else:
            while not stopping():
                time.sleep(0.25)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        logging.exception("Desktop startup failed")
        if not args.headless:
            from tkinter import messagebox
            messagebox.showerror("IPtopo", str(error))
        return 1
    finally:
        if server:
            server.should_exit = True
        if thread:
            thread.join(timeout=15)
        if sock:
            sock.close()
        if args.ready_file:
            args.ready_file.unlink(missing_ok=True)
        if lock:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
