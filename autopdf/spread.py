"""Save each side of a spread with the viewer's image download menu."""
import ctypes
import time

from PIL import Image

from .chrome_capture import assemble
from .existing_tab import scan_folder
from .scanner import ScanCancelled, execute_action


def versions(folder):
    result = {}
    for path in folder.iterdir():
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif"}:
            try:
                stat = path.stat()
                result[path] = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                pass
    return result


def wait_image(folder, before, stop):
    deadline = time.monotonic() + 30
    accepted = False
    while time.monotonic() < deadline:
        if stop.wait(.2):
            raise ScanCancelled()
        changed = [p for p, v in versions(folder).items() if before.get(p) != v]
        if len(changed) > 1:
            raise ValueError("複数の画像が届きました。ほかのダウンロードを止めてください。")
        if changed:
            try:
                with Image.open(changed[0]) as image:
                    return image.convert("RGB")
            except OSError:
                continue
        if not accepted:
            import pyautogui
            user = ctypes.windll.user32
            hwnd = user.GetForegroundWindow()
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, 256)
            if title.value in ("名前を付けて保存", "Save As"):
                pyautogui.press("enter")
                accepted = True
    raise ValueError("画像が届きません。保存先と右クリックメニューの項目番号を確認してください。")


def capture_spread(root, name, count, folder, points, item, actions, confirm, report,
                   stop, delay, select_area, output_size):
    import pyautogui
    job = scan_folder(root / "spread", name)
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda p: int(p.stem[5:]))
    if len(paths) > count or any(p.name != f"page-{i:04}.png" for i,p in enumerate(paths,1)):
        raise ValueError("保存枚数と連番を確認してください。")
    if len(paths) < count:
        confirm(f"次の保存番号は {len(paths)+1}。対象の見開きを表示してください。", None)
    for index in range(len(paths), count):
        if stop.is_set():
            raise ScanCancelled()
        before = versions(folder)
        pyautogui.click(*points[index % 2], button="right")
        time.sleep(.35)
        pyautogui.press("down", presses=item, interval=.08)
        pyautogui.press("enter")
        page = wait_image(folder, before, stop)
        if paths:
            with Image.open(paths[-1]) as last:
                if last.size == page.size and last.convert("RGB").tobytes() == page.tobytes():
                    raise ValueError("直前と同じ画像です。ページ位置と見開き送りを確認してください。")
        path = job / f"page-{index+1:04}.png"
        tmp = path.with_suffix(".tmp")
        page.save(tmp, format="PNG")
        tmp.replace(path)
        paths.append(path)
        report(f"{index+1}/{count} 枚保存　{page.width}×{page.height} px")
        if index == 0 and count > 1:
            confirm("最初のページ番号と画質を確認してください。", path)
        if index % 2 and index+1 < count:
            for action in actions:
                execute_action(action, stop)
            if stop.wait(delay):
                raise ScanCancelled()
    destination = job / "book.pdf"
    assemble(paths, destination)
    return destination
