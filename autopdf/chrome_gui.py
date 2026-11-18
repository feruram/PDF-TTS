import math
import os
import tkinter as tk
import threading
from queue import Empty, Queue
from pathlib import Path
from tkinter import StringVar, filedialog, ttk

from .existing_tab import capture_existing

from PIL import Image, ImageTk

from .chrome_capture import run
from .scanner import ScanCancelled


class ChromeFrame(ttk.Frame):
    def __init__(self, master, root):
        super().__init__(master, padding=16)
        self.root = root
        self.running = False
        self.events = Queue()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.columnconfigure(1, weight=1)
        self.url = StringVar()
        self.count = StringVar()
        self.key = StringVar(value="Right")
        self.position = StringVar()
        for row, (label, var) in enumerate([
            ("ビューアのURL", self.url), ("撮影枚数（再開時は保存済み分を含む）", self.count),
            ("ページ送り", self.key), ("クリック時の座標（x,y）", self.position)
        ]):
            ttk.Label(self, text=label).grid(row=row+1, column=0, sticky="w", pady=4)
            field = (ttk.Combobox(self, textvariable=var, state="readonly",
                                  values=("Right", "Left", "PageDown", "click"))
                     if var is self.key else ttk.Entry(self, textvariable=var))
            field.grid(row=row+1, column=1, sticky="ew", padx=8)
        self.mode = StringVar(value="開いているタブ")
        modes = ttk.Combobox(self, state="readonly", textvariable=self.mode,
                             values=("開いているタブ", "専用Chromeを開く"))
        modes.grid(row=0, column=1, sticky="ew")
        ttk.Label(self, text="撮影方法").grid(row=0, column=0, sticky="w")
        self.existing = ttk.Frame(self)
        self.existing.grid(row=5, columnspan=2, sticky="ew")
        self.existing.columnconfigure(1, weight=1)
        self.name = StringVar()
        self.folder = StringVar(value=str(Path.home() / "Downloads"))
        self.command = StringVar(value="Capture screenshot")
        self.points = {}
        for i, (label, var) in enumerate((("資料名（再開時は同じ名前）", self.name),
                                        ("Chromeのダウンロード先", self.folder),
                                        ("DevToolsの撮影コマンド名（英語）", self.command))):
            ttk.Label(self.existing, text=label).grid(row=i, column=0, sticky="w")
            ttk.Entry(self.existing, textvariable=var).grid(row=i, column=1, sticky="ew")
        ttk.Button(self.existing, text="参照", command=self.select_folder).grid(row=1, column=2)
        for i, label in enumerate(("DevTools内の空白位置", "本文内のクリック可能な余白")):
            var = StringVar(value="画面上で指定")
            ttk.Label(self.existing, text=label).grid(row=4+i, column=0, sticky="w")
            ttk.Button(self.existing, textvariable=var, command=lambda n=i, v=var:
                       self.pick_point(n, v)).grid(row=4+i, column=1, sticky="w")
        ttk.Label(self.existing, text="ページ送りは「スキャン→PDF」で編集中の操作列を使用します。").grid(row=6, columnspan=3)
        def show_mode(*_):
            active = self.mode.get() == "開いているタブ"
            for row in (1, 3, 4):
                for widget in self.grid_slaves(row=row):
                    if active:
                        widget.grid_remove()
            if active:
                self.existing.grid()
            else:
                self.existing.grid_remove()
                for widget in self.manual_fields:
                    widget.grid()
        self.manual_fields = [w for row in (1, 3, 4) for w in self.grid_slaves(row=row)]
        self.mode.trace_add("write", show_mode)
        show_mode()
        bar = ttk.Frame(self)
        bar.grid(row=6, columnspan=2, sticky="w", pady=10)
        self.delay = StringVar(value="1.0")
        ttk.Label(bar, text="ページ送り後の待機（秒）").pack(side="left")
        ttk.Entry(bar, width=6, textvariable=self.delay).pack(side="left", padx=4)
        self.start = ttk.Button(bar, text="スキャン開始", command=self.begin)
        self.next = ttk.Button(bar, text="続行", command=self.proceed, state="disabled")
        self.cancel = ttk.Button(bar, text="中断", command=self.stop.set, state="disabled")
        for button in (self.start, self.next, self.cancel):
            button.pack(side="left", padx=3)
        self.status = StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=760).grid(row=7, columnspan=2, sticky="w")
        self.preview = ttk.Label(self)
        self.preview.grid(row=8, columnspan=2, pady=8)
        self.history = tk.Text(self, height=6, state="disabled", wrap="word")
        self.history.grid(row=9, columnspan=2, sticky="ew")
        self.after(100, self.poll)

    def select_folder(self):
        folder = filedialog.askdirectory(parent=self)
        if folder:
            self.folder.set(folder)

    def pick_point(self, index, var):
        def selected(point):
            self.points[index] = point
            var.set(str(point))
        self.winfo_toplevel().scanner._pick_on_screen(point=True, apply_selection=selected)

    def begin(self):
        url = self.url.get().strip()
        key = self.key.get()
        try:
            count = int(self.count.get())
            if count < 1:
                raise ValueError()
        except ValueError:
            self.status.set("撮影枚数に1以上の整数を入力してください。")
            return
        try:
            delay = float(self.delay.get())
            if not math.isfinite(delay) or not 0 <= delay <= 86400:
                raise ValueError()
        except ValueError:
            self.status.set("ページ送り後の待機は0～86400秒で入力してください。")
            return
        self.use_existing = self.mode.get() == "開いているタブ"
        if self.use_existing:
            name = self.name.get().strip()
            folder = Path(self.folder.get())
            actions = list(self.winfo_toplevel().scanner.actions)
            command = self.command.get().strip()
            if not (name and folder.is_dir() and len(self.points) == 2 and command.isascii() and (actions or count == 1)):
                self.status.set("資料名、ダウンロード先、画面上の2点、英語コマンド、ページ送り操作を確認してください。")
                return
            task = capture_existing
            args = (name, count, folder, self.points[0], self.points[1], command, actions)
        else:
            if not url.startswith(("https://", "http://")):
                self.status.set("http(s)のURLを入力してください。")
                return
            position = None
            if key == "click":
                try:
                    x, y = map(int, self.position.get().split(","))
                    if not (0 <= x < 1400 and 0 <= y < 2000):
                        raise ValueError()
                    position = (x, y)
                except ValueError:
                    self.status.set("1400×2000の表示領域内の座標を x,y で指定してください。")
                    return
            task = run
            args = (url, count, key, position)
        self.preview.configure(image="")
        self.preview.unbind("<Button-1>")
        self.history.configure(state="normal")
        self.history.delete("1.0", "end")
        self.history.configure(state="disabled")
        self.stop.clear()
        self.running = True
        self.start.state(["disabled"])
        self.cancel.state(["!disabled"])
        self.status.set("Chromeを準備中…")
        threading.Thread(target=self.worker, args=(task, delay, *args), daemon=True).start()

    def worker(self, task, delay, *args):
        try:
            path = task(self.root / "output", *args, self.confirm,
                       lambda msg: self.events.put(("status", msg, None)), self.stop, delay, self.select_area)
            result = f"PDF完成　{path}"
        except ScanCancelled:
            result = "中断しました。保存済みのページから再開できます。"
        except Exception as exc:
            result = str(exc)
        self.events.put(("done", result, None))

    def confirm(self, message, path):
        self.ready.clear()
        self.events.put(("confirm", message, path))
        while not self.ready.wait(0.1):
            if self.stop.is_set():
                raise ScanCancelled()
        if self.stop.is_set():
            raise ScanCancelled()

    def select_area(self, path):
        self.ready.clear()
        self.events.put(("area", "PDFに載せる範囲を指定してください。", path))
        while not self.ready.wait(0.1):
            if self.stop.is_set():
                raise ScanCancelled()
        if self.stop.is_set():
            raise ScanCancelled()
        return self.area

    def choose_area(self, path):
        dialog = tk.Toplevel(self)
        dialog.title("PDFに載せる範囲")
        ttk.Label(dialog, text="本のページをドラッグで囲んでください。同じ範囲を全ページに適用します。").pack()
        with Image.open(path) as source:
            width, height = source.size
            source.thumbnail((int(self.winfo_screenwidth()*0.8), int(self.winfo_screenheight()*0.7)))
            photo = ImageTk.PhotoImage(source)
        canvas = tk.Canvas(dialog, width=photo.width(), height=photo.height(), highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.photo = photo
        start = []
        def press(event):
            start[:] = [event.x, event.y]
            canvas.delete("selection")
            canvas.create_rectangle(*start, *start, outline="red", width=2, tags="selection")
            apply.state(["disabled"])
        def drag(event):
            if start:
                canvas.coords("selection", *start, event.x, event.y)
        def release(event):
            drag(event)
            if start and abs(event.x-start[0]) > 2 and abs(event.y-start[1]) > 2:
                apply.state(["!disabled"])
        def finish(crop):
            self.area = None
            if crop:
                x1, y1, x2, y2 = canvas.coords("selection")
                self.area = (max(0, math.floor(min(x1,x2)*width/photo.width())),
                             max(0, math.floor(min(y1,y2)*height/photo.height())),
                             min(width, math.ceil(max(x1,x2)*width/photo.width())),
                             min(height, math.ceil(max(y1,y2)*height/photo.height())))
            dialog.destroy()
            self.proceed()
        def cancel():
            self.stop.set()
            dialog.destroy()
        canvas.bind("<Button-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        controls = ttk.Frame(dialog)
        controls.pack()
        apply = ttk.Button(controls, text="選択範囲で続行", command=lambda: finish(True), state="disabled")
        apply.pack(side="left")
        ttk.Button(controls, text="画像全体で続行", command=lambda: finish(False)).pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.transient(self.winfo_toplevel())
        dialog.lift()
        dialog.wait_visibility()
        dialog.grab_set()
        dialog.focus_set()

    def proceed(self):
        self.next.state(["disabled"])
        if self.use_existing:
            window = self.winfo_toplevel()
            window.iconify()
            self.after(350, self.ready.set)
        else:
            self.ready.set()

    def poll(self):
        try:
            while True:
                kind, msg, path = self.events.get_nowait()
                self.status.set(msg)
                self.history.configure(state="normal")
                self.history.insert("end", msg + "\n")
                self.history.see("end")
                self.history.configure(state="disabled")
                if self.use_existing and kind in ("confirm", "area", "done"):
                    self.winfo_toplevel().deiconify()
                if kind == "area":
                    self.after_idle(lambda p=path: self.choose_area(p))
                elif kind == "confirm":
                    if path:
                        with Image.open(path) as image:
                            image.thumbnail((420, 440))
                            self.photo = ImageTk.PhotoImage(image)
                        self.preview.configure(image=self.photo)
                        self.preview.bind("<Button-1>", lambda event, p=path: os.startfile(p))
                    self.next.state(["!disabled"])
                elif kind == "done":
                    self.running = False
                    self.start.state(["!disabled"])
                    self.next.state(["disabled"])
                    self.cancel.state(["disabled"])
        except Empty:
            pass
        self.after(100, self.poll)
