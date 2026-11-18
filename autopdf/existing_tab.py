"""Capture the viewport of a user-prepared DevTools session without navigating."""
import hashlib
import shutil
import time

from PIL import Image

from .chrome_capture import assemble, thumbnail
from .scanner import ScanCancelled, execute_action, page_difference_percent


def wait_for_capture(folder, existing, size, stop):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if stop.wait(0.2):
            raise ScanCancelled()
        candidates = set(folder.glob("*.png")) - existing
        if len(candidates) > 1:
            raise ValueError("複数のPNGを検出しました。撮影中は他のダウンロードを停止してください。")
        for path in candidates:
            try:
                with Image.open(path) as image:
                    image.load()
                    actual = image.size
            except OSError:
                continue
            if actual != size:
                raise ValueError(f"撮影結果は {actual[0]}×{actual[1]} px です。指定した画素数、DevToolsの表示サイズ、DPRを確認してください。")
            return path
    raise ValueError("PNGが届きません。DevToolsのクリック位置、コマンド名、Chromeの保存先と保存先確認設定を確認してください。")


def capture_existing(root, name, count, folder, size, devtools, reader, command,
                     actions, confirm, report, stop, delay=1.0, select_area=None):
    import pyautogui as mouse
    job = root / ("tab-" + hashlib.sha256(name.encode()).hexdigest()[:16])
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda p: int(p.stem[5:]))
    if len(paths) > count or any(p.name != f"page-{n:04}.png" for n, p in enumerate(paths, 1)):
        raise ValueError("保存済み枚数と連番を確認してください。")
    if paths:
        with Image.open(paths[-1]) as image:
            if image.size != size:
                raise ValueError("再開時は前回と同じ画素数を指定してください。")
    previous = thumbnail(paths[-1].read_bytes()) if paths else None
    area = select_area(paths[0]) if paths and select_area else None
    if len(paths) < count:
        confirm(f"閲覧画面の {len(paths)+1} 枚目とDevToolsを表示し、続行してください。", None)
    for number in range(len(paths)+1, count+1):
        existing = set(folder.glob("*.png"))
        mouse.click(*devtools)
        mouse.hotkey("ctrl", "shift", "p")
        time.sleep(0.35)
        mouse.write(command, interval=0.025)
        time.sleep(0.3)
        mouse.press("enter")
        source = wait_for_capture(folder, existing, size, stop)
        sample = thumbnail(source.read_bytes())
        if previous is not None and page_difference_percent(previous, sample) <= 0.15:
            raise ValueError("前ページから変化を確認できません。開始ページとページ送り操作を確認してください。")
        previous = sample
        path = job / f"page-{number:04}.png"
        tmp = path.with_suffix(".tmp")
        shutil.copyfile(source, tmp)
        tmp.replace(path)
        paths.append(path)
        if number == 1 and select_area:
            area = select_area(path)
        report(f"撮影済み {number} / {count} 枚")
        if number < count:
            if number == 1 and not select_area:
                confirm(f"初回の画像は {size[0]}×{size[1]} px です。画質を確認してください。", path)
            mouse.click(*reader)
            for action in actions:
                if stop.is_set():
                    raise ScanCancelled()
                execute_action(action, stop)
            if stop.wait(delay):
                raise ScanCancelled()
    report("PDFを作成中…")
    destination = job / "book.pdf"
    assemble(paths, destination, area)
    return destination
