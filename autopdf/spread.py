"""Save each side of a spread with the viewer's image download menu."""
import ctypes
import re
import time

from PIL import Image

from .chrome_capture import assemble
from .existing_tab import scan_folder
from .scanner import ScanCancelled, execute_action


def close_download_popup():
    import win32gui
    import pyautogui
    hwnd = win32gui.GetForegroundWindow()
    if win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin") and win32gui.GetWindowText(hwnd) in (
        "最近のダウンロード履歴", "Recent downloads", "Recent download history",
    ):
        pyautogui.press("esc")
        time.sleep(.3)


def image_save_item():
    import win32gui
    import win32process
    from pywinauto import Desktop
    hwnd = win32gui.GetForegroundWindow()
    if not win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin"):
        return None
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    labels = {"名前を付けて画像を保存", "画像に名前を付けて保存", "save image as"}
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    from pywinauto.controls.uiawrapper import UIAWrapper
    uia = IUIA()
    api = uia.iuia
    kind = api.CreatePropertyCondition(uia.UIA_dll.UIA_ControlTypePropertyId,
                                       uia.UIA_dll.UIA_MenuItemControlTypeId)
    names = ("名前を付けて画像を保存(V)…", "名前を付けて画像を保存…",
             "Save image as…", "Save image as...", "Save image as")
    match = None
    for label in names:
        condition = api.CreatePropertyCondition(uia.UIA_dll.UIA_NamePropertyId, label)
        match = condition if match is None else api.CreateOrCondition(match, condition)
    target = api.ElementFromHandle(hwnd).FindFirst(4, api.CreateAndCondition(kind, match))
    if target:
        windows_items = [[UIAWrapper(UIAElementInfo(target))]]
    else:
        # Other localized accelerator spellings and detached menus.
        windows_items = (w.descendants(control_type="MenuItem") for w in
                         Desktop(backend="uia").windows(process=pid, visible_only=True))
    for items in windows_items:
        for item in items:
            name = item.window_text().lower().split("\t")[0].rstrip("…. ").replace("&", "")
            name = re.sub(r"\([a-z]\)$", "", name)
            if name not in labels or not item.is_enabled() or not item.is_visible():
                continue
            parent = item.parent()
            while parent and parent.element_info.control_type not in ("Document", "Window"):
                parent = parent.parent()
            if parent and parent.element_info.control_type != "Document":
                return item
    return None


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
    raise ValueError("画像が届きません。保存先とChromeの保存ダイアログを確認してください。")


def capture_spread(root, name, count, folder, points, actions, confirm, report,
                   stop, delay, select_area, output_size, *, cover=False):
    import pyautogui
    job = scan_folder(root / "spread", name)
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda p: int(p.stem[5:]))
    if len(paths) > count or any(p.name != f"page-{i:04}.png" for i,p in enumerate(paths,1)):
        raise ValueError("保存枚数と連番を確認してください。")
    # Old jobs always started with both pages of a spread.
    layout = job / "spread-layout.txt"
    if layout.exists():
        saved = layout.read_text(encoding="ascii").strip()
        if saved not in ("cover", "pair"):
            raise ValueError("保存済み資料の表紙設定を確認してください。")
        cover = saved == "cover"
    elif paths:
        cover = False
    else:
        tmp = layout.with_suffix(".tmp")
        tmp.write_text("cover" if cover else "pair", encoding="ascii")
        tmp.replace(layout)
    order = "表紙1枚 → 見開き" if cover else "見開きから"
    report(f"保存順：{order}")
    if len(paths) < count:
        next_slot = (len(paths) + int(cover)) % 2
        confirm(f"次の保存番号は {len(paths)+1}。位置{next_slot+1}に次のページが来る画面を表示してください。", None)
    for index in range(len(paths), count):
        if stop.is_set():
            raise ScanCancelled()
        slot = (index + int(cover)) % 2
        before = versions(folder)
        import pythoncom
        pythoncom.CoInitialize()
        try:
            for attempt in range(2):
                close_download_popup()
                pyautogui.click(*points[slot], button="right")
                if stop.wait(.05):
                    raise ScanCancelled()
                menu_item = image_save_item()
                if menu_item is not None:
                    break
                pyautogui.press("esc")
                if stop.wait(.6):
                    raise ScanCancelled()
            if menu_item is None:
                pyautogui.press("esc")
                raise ValueError(f"{index+1}枚目の位置で「名前を付けて画像を保存」が見つかりません。"
                                 "DevToolsを右に表示し、端末エミュレーションをオフにして、本文内の位置を再指定してください。")
            menu_item.invoke()
        finally:
            pythoncom.CoUninitialize()
        page = wait_image(folder, before, stop)
        for earlier in paths[-2:]:
            with Image.open(earlier) as last:
                if last.size == page.size and last.convert("RGB").tobytes() == page.tobytes():
                    raise ValueError("直近の見開きと重複する画像です。ページ位置と見開き送りを確認してください。")
        path = job / f"page-{index+1:04}.png"
        tmp = path.with_suffix(".tmp")
        page.save(tmp, format="PNG")
        tmp.replace(path)
        paths.append(path)
        report(f"{index+1}/{count} 枚保存　{page.width}×{page.height} px")
        if index == 0 and count > 1:
            confirm("最初のページ番号と画質を確認してください。", path)
        if slot == 1 and index+1 < count:
            if stop.wait(.8):
                raise ScanCancelled()
            close_download_popup()
            for action in actions:
                execute_action(action, stop)
            if stop.wait(delay):
                raise ScanCancelled()
    destination = job / "book.pdf"
    assemble(paths, destination)
    return destination
