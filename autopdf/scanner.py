from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab, ImageStat

from .models import PageTurnAction, ScanProfile


ProgressCallback = Callable[[str, int, int], None]


class ScanCancelled(Exception):
    pass


def _interruptible_wait(seconds: float, stop_event: threading.Event) -> None:
    if stop_event.wait(max(0.0, seconds)):
        raise ScanCancelled


def page_difference_percent(previous: Image.Image, current: Image.Image) -> float:
    """Return a cheap visual difference score in the range 0..100."""

    size = (160, 160)
    a = previous.convert("L").resize(size)
    b = current.convert("L").resize(size)
    stat = ImageStat.Stat(ImageChops.difference(a, b))
    return float(stat.mean[0]) / 255.0 * 100.0


def execute_action(action: PageTurnAction, stop_event: threading.Event) -> None:
    if action.kind == "wait":
        _interruptible_wait(float(action.params.get("seconds", 0)), stop_event)
        return

    import pyautogui

    p = action.params
    if action.kind == "key":
        pyautogui.press(str(p.get("key", "right")))
    elif action.kind == "click":
        pyautogui.click(
            int(p["x"]), int(p["y"]), button=str(p.get("button", "left"))
        )
    elif action.kind == "double_click":
        pyautogui.doubleClick(int(p["x"]), int(p["y"]), interval=0.12)
    elif action.kind == "drag":
        pyautogui.moveTo(int(p["x1"]), int(p["y1"]))
        pyautogui.dragTo(
            int(p["x2"]),
            int(p["y2"]),
            duration=float(p.get("duration", 0.3)),
            button=str(p.get("button", "left")),
        )
    elif action.kind == "scroll":
        if "x" in p and "y" in p:
            pyautogui.moveTo(int(p["x"]), int(p["y"]))
        pyautogui.scroll(int(p.get("clicks", -3)))
    else:
        raise ValueError(f"未対応のページめくり操作です: {action.kind}")


def images_to_pdf(
    image_paths: list[Path], output_path: Path, *, page_size: tuple[float, float] | None = None
) -> None:
    if not image_paths:
        raise ValueError("PDFに変換する画像がありません。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import img2pdf
    except ImportError:
        if page_size is not None:
            raise RuntimeError("A4への配置には img2pdf が必要です。") from None
    else:
        options = {}
        if page_size is not None:
            options["layout_fun"] = img2pdf.get_layout_fun(
                pagesize=page_size, fit=img2pdf.FitMode.into
            )
        with output_path.open("wb") as stream:
            img2pdf.convert(*(str(path) for path in image_paths), outputstream=stream, **options)
        return

    images = [Image.open(path).convert("RGB") for path in image_paths]
    try:
        images[0].save(
            output_path,
            "PDF",
            save_all=True,
            append_images=images[1:],
            resolution=300,
        )
    finally:
        for image in images:
            image.close()


class ScanEngine:
    def __init__(self, project_root: Path):
        self.project_root = project_root

    def run(
        self,
        profile: ScanProfile,
        stop_event: threading.Event,
        progress: ProgressCallback,
    ) -> Path:
        profile.validate()
        output_dir = profile.output_path(self.project_root)
        image_dir = output_dir / Path(profile.pdf_name).stem
        image_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / profile.pdf_name

        progress("開始待機中。対象の文書画面を前面にしてください。", 0, profile.page_count)
        _interruptible_wait(profile.start_delay, stop_event)

        previous: Image.Image | None = None
        paths: list[Path] = []
        for index in range(profile.page_count):
            if stop_event.is_set():
                raise ScanCancelled
            raw = ImageGrab.grab(
                bbox=(profile.left, profile.top, profile.right, profile.bottom),
                all_screens=True,
            ).convert("RGB")
            if previous is not None and profile.stop_on_duplicate:
                difference = page_difference_percent(previous, raw)
                if difference < profile.duplicate_threshold:
                    progress(
                        f"前ページとの差が{difference:.3f}%のため撮影を停止しました。",
                        index,
                        profile.page_count,
                    )
                    break
            previous = raw.copy()
            image_path = image_dir / f"{profile.file_prefix}_{index + 1:04d}.jpg"
            raw.save(
                image_path,
                "JPEG",
                quality=max(40, min(100, profile.jpeg_quality)),
                subsampling=0,
                optimize=True,
                dpi=(300, 300),
            )
            paths.append(image_path)
            progress("ページを保存しました。", index + 1, profile.page_count)
            if index + 1 < profile.page_count:
                for action in profile.actions:
                    execute_action(action, stop_event)

        if not paths:
            raise ScanCancelled
        progress("PDFを作成しています。", len(paths), profile.page_count)
        page_size = None
        if profile.fit_a4:
            page_size = (profile.a4_width * 72 / 300, profile.a4_height * 72 / 300)
        images_to_pdf(paths, pdf_path, page_size=page_size)
        progress("PDFを作成しました。", len(paths), len(paths))
        return pdf_path


class PageTurnRecorder:
    """Record one real page-turn gesture until F8 is released."""

    def __init__(self):
        self.actions: list[PageTurnAction] = []
        self._last_time: float | None = None
        self._mouse_down: tuple[float, int, int, str] | None = None
        self._keyboard_listener = None
        self._mouse_listener = None
        self._lock = threading.Lock()
        self.done = threading.Event()

    @staticmethod
    def _button_name(button) -> str:
        value = str(button).split(".")[-1]
        return value if value in {"left", "right", "middle"} else "left"

    @staticmethod
    def _key_name(key) -> str:
        char = getattr(key, "char", None)
        if char:
            return char
        name = str(key).replace("Key.", "")
        aliases = {"page_down": "pagedown", "page_up": "pageup"}
        return aliases.get(name, name)

    def _add_timed(self, action: PageTurnAction, event_time: float) -> None:
        with self._lock:
            if self._last_time is not None:
                delay = event_time - self._last_time
                if delay >= 0.05:
                    self.actions.append(
                        PageTurnAction("wait", {"seconds": round(min(delay, 10.0), 3)})
                    )
            self.actions.append(action)
            self._last_time = event_time

    def _on_click(self, x, y, button, pressed):
        now = time.monotonic()
        button_name = self._button_name(button)
        if pressed:
            self._mouse_down = (now, int(x), int(y), button_name)
            return
        if self._mouse_down is None:
            return
        started, x1, y1, started_button = self._mouse_down
        self._mouse_down = None
        distance = abs(int(x) - x1) + abs(int(y) - y1)
        if distance >= 8:
            action = PageTurnAction(
                "drag",
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": int(x),
                    "y2": int(y),
                    "duration": round(max(0.1, now - started), 3),
                    "button": started_button,
                },
            )
        else:
            action = PageTurnAction(
                "click", {"x": int(x), "y": int(y), "button": started_button}
            )
        self._add_timed(action, now)

    def _on_scroll(self, x, y, dx, dy):
        self._add_timed(
            PageTurnAction(
                "scroll", {"x": int(x), "y": int(y), "clicks": int(dy)}
            ),
            time.monotonic(),
        )

    def _on_release(self, key):
        from pynput import keyboard

        if key == keyboard.Key.f8:
            self.done.set()
            return False
        self._add_timed(
            PageTurnAction("key", {"key": self._key_name(key)}), time.monotonic()
        )

    def record(self, ready_delay: float = 2.0, timeout: float = 60.0) -> list[PageTurnAction]:
        try:
            from pynput import keyboard, mouse
        except ImportError as exc:
            raise RuntimeError("操作記録には pynput のインストールが必要です。") from exc

        time.sleep(ready_delay)
        self._last_time = None
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll
        )
        self._keyboard_listener = keyboard.Listener(on_release=self._on_release)
        self._mouse_listener.start()
        self._keyboard_listener.start()
        self.done.wait(timeout)
        self._mouse_listener.stop()
        self._keyboard_listener.stop()
        if not self.done.is_set():
            raise TimeoutError("操作記録が60秒でタイムアウトしました。")
        with self._lock:
            return list(self.actions)

