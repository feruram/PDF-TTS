"""Scan the foreground Android emulator display using SDK Platform Tools."""
import io
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from .chrome_capture import assemble, save_page
from .existing_tab import scan_folder
from .scanner import ScanCancelled, page_difference_percent


def adb_command(*args):
    exe = shutil.which("adb")
    if not exe:
        for base in (os.getenv("ANDROID_HOME"), os.getenv("ANDROID_SDK_ROOT"),
                     Path.home() / "AppData/Local/Android/Sdk"):
            if base and (Path(base) / "platform-tools/adb.exe").is_file():
                exe = str(Path(base) / "platform-tools/adb.exe")
                break
    if not exe:
        raise ValueError("Android SDK Platform-Tools の adb が見つかりません。SDKの場所をANDROID_HOMEに指定できます。")
    result = subprocess.run([exe, *args], capture_output=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace"))
    return result.stdout


def capture_emulator(root, name, count, direction, confirm, report, stop, delay, select_area, output_size):
    job = scan_folder(root / "android", name)
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda path: int(path.stem[5:]))
    if len(paths) > count or any(p.name != f"page-{i:04}.png" for i,p in enumerate(paths,1)):
        raise ValueError("保存済み枚数と連番を確認してください。")
    area = select_area(paths[0]) if paths else None
    if len(paths) < count:
        devices = [parts[0] for line in adb_command("devices").decode().splitlines()
                   if len(parts := line.split()) == 2 and parts[0].startswith("emulator-") and parts[1] == "device"]
        if len(devices) != 1:
            raise ValueError("Android Studioで縦向きのエミュレーターを1台起動してください。")
        serial = devices[0]
        confirm(f"エミュレーターに次の単ページを表示してください。次の保存番号は {len(paths)+1} です。", None)
    previous = None
    if paths:
        with Image.open(paths[-1]) as image:
            previous = image.convert("L").resize((160,160))
    for n in range(len(paths)+1, count+1):
        if stop.is_set():
            raise ScanCancelled()
        data = adb_command("-s", serial, "exec-out", "screencap", "-p")
        with Image.open(io.BytesIO(data)) as image:
            w,h = image.size
            if w >= h:
                raise ValueError("エミュレーターを縦向きにしてください。")
            path = job / f"page-{n:04}.png"
            temp = path.with_suffix(".tmp")
            save_page(image, temp, output_size)
        with Image.open(temp) as saved:
            current = saved.convert("L").resize((160,160))
        if previous is not None and page_difference_percent(previous,current) <= 0.15:
            temp.unlink()
            raise ValueError(f"{n}枚目のページ変化を確認できません。送り方向と待機秒数を確認してください。")
        previous = current
        temp.replace(path)
        paths.append(path)
        report(f"撮影済み {n}/{count} 枚")
        if n == 1:
            area = select_area(path)
        if n < count:
            x1,x2 = (w*4//5,w//5) if direction == "左へスワイプ" else (w//5,w*4//5)
            adb_command("-s",serial,"shell","input","swipe",str(x1),str(h//2),str(x2),str(h//2),"320")
            if stop.wait(delay):
                raise ScanCancelled()
    destination = job / "book.pdf"
    assemble(paths, destination, area, output_size)
    return destination
