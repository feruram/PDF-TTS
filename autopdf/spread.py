"""Save each side of a spread with the viewer's image download menu."""
import re
import time

from PIL import Image

from .chrome_capture import assemble
from .existing_tab import scan_folder
from .scanner import ScanCancelled, execute_action


def prepare_browser_input(point=None, browser=None):
    import win32con
    import win32gui
    import win32process

    titles = {"最近のダウンロード履歴", "Recent downloads", "Recent download history"}
    def target():
        if point is None:
            return win32gui.GetForegroundWindow()
        return win32gui.GetAncestor(win32gui.WindowFromPoint(point), win32con.GA_ROOT)

    hwnd = browser or target()
    if not hwnd or not win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin"):
        raise ValueError("操作位置にChromeがありません。教本を前面にして再開してください。")
    pid = win32process.GetWindowThreadProcessId(hwnd)[1]
    def dismiss(candidate, _):
        if (win32gui.IsWindowVisible(candidate)
                and win32gui.GetClassName(candidate).startswith("Chrome_WidgetWin")
                and win32process.GetWindowThreadProcessId(candidate)[1] == pid
                and win32gui.GetWindowText(candidate) in titles):
            win32gui.PostMessage(candidate, win32con.WM_CLOSE, 0, 0)
            closed.append(candidate)
        return True

    closed = []
    win32gui.EnumWindows(dismiss, None)
    deadline = time.monotonic() + 1
    while any(win32gui.IsWindowVisible(w) for w in closed) and time.monotonic() < deadline:
        time.sleep(.03)
    hwnd = target()
    if (not hwnd or not win32gui.GetClassName(hwnd).startswith("Chrome_WidgetWin")
            or win32gui.GetWindowText(hwnd) in titles
            or win32process.GetWindowThreadProcessId(hwnd)[1] != pid
            or (browser is not None and hwnd != browser)):
        raise ValueError("教本の操作位置が別のウィンドウに隠れています。位置を確認して再開してください。")
    return hwnd


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


def click_save_button(owner):
    import win32con
    import win32gui

    dialogs = []
    def collect(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd)
                and win32gui.GetClassName(hwnd) == "#32770"
                and win32gui.GetWindow(hwnd, win32con.GW_OWNER) == owner
                and win32gui.GetWindowText(hwnd) in ("名前を付けて保存", "Save As")):
            dialogs.append(hwnd)
        return True
    win32gui.EnumWindows(collect, None)
    if len(dialogs) == 1:
        button = win32gui.GetDlgItem(dialogs[0], win32con.IDOK)
        if (button and win32gui.IsWindowEnabled(button)
                and win32gui.GetClassName(button) == "Button"
                and win32gui.GetWindowText(button).replace("&", "").lower() in ("保存", "保存(s)", "save")):
            win32gui.PostMessage(button, win32con.BM_CLICK, 0, 0)


def wait_image(folder, before, stop, owner):
    deadline = time.monotonic() + 30
    next_click = 0
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
        if time.monotonic() >= next_click:
            click_save_button(owner)
            next_click = time.monotonic() + 1
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
    browser = None
    for index in range(len(paths), count):
        if stop.is_set():
            raise ScanCancelled()
        slot = (index + int(cover)) % 2
        before = versions(folder)
        import pythoncom
        pythoncom.CoInitialize()
        try:
            for attempt in range(2):
                browser = prepare_browser_input(points[slot], browser)
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
            import win32gui
            owner = win32gui.GetForegroundWindow()
            # A synchronous Invoke can wait for the modal save dialog to close.
            menu_item.click_input()
        finally:
            pythoncom.CoUninitialize()
        page = wait_image(folder, before, stop, owner)
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
            for action in actions:
                params = action.params
                if action.kind in ("click", "double_click") or (action.kind == "scroll" and "x" in params):
                    prepare_browser_input((int(params["x"]), int(params["y"])), browser)
                elif action.kind == "drag":
                    for x, y in (("x1", "y1"), ("x2", "y2")):
                        prepare_browser_input((int(params[x]), int(params[y])), browser)
                elif action.kind != "wait":
                    prepare_browser_input(browser=browser)
                execute_action(action, stop)
            if stop.wait(delay):
                raise ScanCancelled()
    destination = job / "book.pdf"
    assemble(paths, destination)
    return destination
