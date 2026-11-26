"""Run the Windows release's local Qwen model for one PDF analysis."""
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .layout_filter import OllamaContentFilter


def bundle_directory():
    app = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return app / "qwen"


def use_bundle(url, model):
    return (url.rstrip("/") in {"http://127.0.0.1:11434", "http://localhost:11434"}
            and model == "qwen3.5:2b" and bundle_directory().is_dir())


@contextmanager
def open_filter(url, model):
    if not use_bundle(url, model):
        yield OllamaContentFilter(url, model)
        return

    root = bundle_directory()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        host = f"127.0.0.1:{probe.getsockname()[1]}"
    env = {**os.environ, "OLLAMA_HOST": host, "OLLAMA_MODELS": str(root / "models"),
           "OLLAMA_NO_CLOUD": "1"}
    with subprocess.Popen([str(root / "ollama.exe"), "serve"], cwd=root, env=env,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as server:
        import win32job
        import win32api
        import win32con
        job = win32job.CreateJobObject(None, "")
        try:
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            process = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, server.pid)
            try:
                win32job.AssignProcessToJobObject(job, process)
            finally:
                process.Close()
            client = OllamaContentFilter("http://" + host, model, timeout=2)
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("同梱のOllamaを起動できません。ZIPをすべて展開してください。")
                try:
                    models = client.list_models()
                except RuntimeError:
                    time.sleep(.1)
                    continue
                if model not in models:
                    raise RuntimeError("同梱のQwenモデルが見つかりません。ZIPをすべて展開してください。")
                break
            else:
                raise RuntimeError("同梱のOllamaが起動時間内に応答しませんでした。")
            client.timeout = 600
            yield client
        finally:
            job.Close()
            if server.poll() is None:
                server.terminate()
