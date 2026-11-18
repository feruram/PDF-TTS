from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

from PIL import ImageGrab, ImageTk


def enable_screen_dpi_awareness() -> None:
    if sys.platform == "win32":
        # Use physical pixels for screenshots, pointer positions and replay.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


class ScreenPicker(tk.Toplevel):
    """Select physical screen coordinates on a frozen desktop image."""

    def __init__(self, parent, *, point: bool, previous=None):
        user32 = ctypes.windll.user32
        self.left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        self.top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        with ImageGrab.grab(all_screens=True) as screenshot:
            super().__init__(parent)
            try:
                self.withdraw()
                self._build(screenshot, point, previous)
            except Exception:
                self.destroy()
                raise

    def _build(self, screenshot, point, previous):
        self.width, self.height = screenshot.size
        self.point = point
        self.result: tuple[int, ...] | None = None
        self.start: tuple[int, int] | None = None
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{self.width}x{self.height}+0+0")
        self.canvas = tk.Canvas(self, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.photo = ImageTk.PhotoImage(screenshot, master=self)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        if previous:
            x1, y1, x2, y2 = previous
            self.canvas.create_rectangle(
                x1 - self.left, y1 - self.top, x2 - self.left, y2 - self.top,
                outline="#ffbf69", width=3, dash=(8, 5), tags="previous",
            )
        self.canvas.bind("<Motion>", self._move)
        if point:
            self.bind("<F8>", self._save_point)
        else:
            self.canvas.bind("<ButtonPress-1>", self._begin_drag)
            self.canvas.bind("<ButtonRelease-1>", self._save_area)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.deiconify()
        self.update_idletasks()
        # Tk interprets negative geometry offsets relative to the right/bottom
        # edge. Position the native wrapper explicitly for left/up monitors.
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        window = user32.GetAncestor(self.winfo_id(), 2)
        set_position = user32.SetWindowPos
        set_position.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        if not window or not set_position(window, -1, self.left, self.top, self.width, self.height, 0x40):
            raise RuntimeError("画面上にオーバーレイを配置できませんでした。")
        self.focus_force()
        self.grab_set()
        x, y = self.winfo_pointerxy()
        self._draw(x - self.left, y - self.top)

    def _move(self, event):
        self._draw(event.x, event.y)

    def _draw(self, x, y):
        canvas = self.canvas
        canvas.delete("marker")
        accent = "#57e5f4"
        if self.start is not None:
            canvas.create_rectangle(*self.start, x, y, outline=accent, width=3, tags="marker")
        for coords in ((x - 16, y, x + 16, y), (x, y - 16, x, y + 16)):
            canvas.create_line(*coords, fill="#14202a", width=5, tags="marker")
            canvas.create_line(*coords, fill=accent, width=2, tags="marker")
        instruction = "F8  現在の位置を保存" if self.point else "ドラッグで撮影範囲を選択"
        label = canvas.create_text(
            x, y, anchor="nw",
            text=f"{instruction}   Esc  キャンセル\nX {x + self.left}   Y {y + self.top}",
            fill="white", font=("Yu Gothic UI", 12), tags="marker",
        )
        x1, y1, x2, y2 = canvas.bbox(label)
        dx = 22 if x2 + 28 < self.width else x1 - x2 - 24
        dy = 22 if y2 + 28 < self.height else y1 - y2 - 24
        canvas.move(label, dx, dy)
        background = canvas.create_rectangle(
            x1 + dx - 6, y1 + dy - 4, x2 + dx + 6, y2 + dy + 4,
            fill="#14202a", outline="", tags="marker",
        )
        canvas.tag_lower(background, label)

    def _begin_drag(self, event):
        self.start = (event.x, event.y)
        self.canvas.delete("previous")

    def _save_area(self, event):
        if self.start is None:
            return
        x, y = self.start
        left, right = sorted((x, min(max(event.x, 0), self.width)))
        top, bottom = sorted((y, min(max(event.y, 0), self.height)))
        if left < right and top < bottom:
            self.result = (left + self.left, top + self.top, right + self.left, bottom + self.top)
            self.destroy()
        else:
            self.start = None

    def _save_point(self, _event):
        self.result = self.winfo_pointerxy()
        self.destroy()
