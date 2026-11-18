"""Capture the viewport of a user-prepared DevTools session without navigating."""
import hashlib
import time

from PIL import Image

from .chrome_capture import assemble, save_page, thumbnail
from .scanner import ScanCancelled, execute_action, page_difference_percent


def png_versions(folder):
    versions = {}
    for path in folder.glob("*.png"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        versions[path] = (stat.st_mtime_ns, stat.st_size)
    return versions


def wait_for_capture(folder, existing, stop):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if stop.wait(0.2):
            raise ScanCancelled()
        candidates = [p for p, version in png_versions(folder).items() if existing.get(p) != version]
        if len(candidates) > 1:
            raise ValueError("複数のPNGを検出しました。撮影中は他のダウンロードを停止してください。")
        for path in candidates:
            try:
                with Image.open(path) as image:
                    image.load()
                    actual = image.size
            except OSError:
                continue
            return path, actual
    raise ValueError("PNGが届きません。DevToolsのクリック位置、コマンド名、Chromeの保存先と保存先確認設定を確認してください。")


def capture_existing(root, name, count, folder, devtools, reader, command,
                     actions, confirm, report, stop, delay=1.0, select_area=None, output_size=(1400, 2000)):
    import pyautogui as mouse
    job = root / ("tab-" + hashlib.sha256(name.encode()).hexdigest()[:16])
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda p: int(p.stem[5:]))
    if len(paths) > count or any(p.name != f"page-{n:04}.png" for n, p in enumerate(paths, 1)):
        raise ValueError("保存済み枚数と連番を確認してください。")
    previous = thumbnail(paths[-1].read_bytes()) if paths else None
    area = select_area(paths[0]) if paths and select_area else None
    if len(paths) < count:
        confirm(f"閲覧画面の {len(paths)+1} 枚目とDevToolsを表示し、続行してください。", None)
    for number in range(len(paths)+1, count+1):
        if stop.is_set():
            raise ScanCancelled()
        report(f"{number}/{count}枚目：DevToolsに撮影コマンドを送信中")
        existing = png_versions(folder)
        mouse.click(*devtools)
        mouse.hotkey("ctrl", "shift", "p")
        time.sleep(0.35)
        mouse.write(command, interval=0.025)
        time.sleep(0.3)
        mouse.press("enter")
        report(f"{number}/{count}枚目：撮影コマンド送信済み、PNGを待機中")
        try:
            source, size = wait_for_capture(folder, existing, stop)
        except ValueError as exc:
            raise ValueError(f"{number}/{count}枚目（保存済み{len(paths)}枚）：{exc}") from exc
        if number == 1:
            report(f"撮影解像度：{size[0]}×{size[1]} px → 保存：{output_size[0]}×{output_size[1]} px")
        sample = thumbnail(source.read_bytes())
        if previous is not None and page_difference_percent(previous, sample) <= 0.15:
            raise ValueError(f"{number}/{count}枚目のPNGを受信しましたが、前ページとの差が小さいため停止しました。保存済み{len(paths)}枚。ページ送り操作と待機時間を確認してください。")
        previous = sample
        path = job / f"page-{number:04}.png"
        tmp = path.with_suffix(".tmp")
        with Image.open(source) as image:
            save_page(image, tmp, output_size)
        tmp.replace(path)
        paths.append(path)
        if number == 1 and select_area:
            area = select_area(path)
        report(f"撮影済み {number} / {count} 枚")
        if number < count:
            if number == 1 and not select_area:
                confirm(f"初回の画像は {size[0]}×{size[1]} px です。画質を確認してください。", path)
            report(f"{number+1}/{count}枚目へのページ送り中")
            mouse.click(*reader)
            for action in actions:
                if stop.is_set():
                    raise ScanCancelled()
                execute_action(action, stop)
            if stop.wait(delay):
                raise ScanCancelled()
    report("PDFを作成中…")
    destination = job / "book.pdf"
    assemble(paths, destination, area, output_size)
    return destination
