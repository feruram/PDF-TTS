import os
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
        self.size = StringVar(value="1400x2000")
        self.folder = StringVar(value=str(Path.home() / "Downloads"))
        self.command = StringVar(value="Capture screenshot")
        self.points = {}
        for i, (label, var) in enumerate((("資料名（再開時は同じ名前）", self.name),
                                        ("保存PNGの幅×高さ（px）", self.size),
                                        ("Chromeのダウンロード先", self.folder),
                                        ("DevToolsの撮影コマンド名（英語）", self.command))):
            ttk.Label(self.existing, text=label).grid(row=i, column=0, sticky="w")
            ttk.Entry(self.existing, textvariable=var).grid(row=i, column=1, sticky="ew")
        ttk.Button(self.existing, text="参照", command=self.select_folder).grid(row=2, column=2)
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
        self.start = ttk.Button(bar, text="スキャン開始", command=self.begin)
        self.next = ttk.Button(bar, text="続行", command=self.proceed, state="disabled")
        self.cancel = ttk.Button(bar, text="中断", command=self.stop.set, state="disabled")
        for button in (self.start, self.next, self.cancel):
            button.pack(side="left", padx=3)
        self.status = StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=760).grid(row=7, columnspan=2, sticky="w")
        self.preview = ttk.Label(self)
        self.preview.grid(row=8, columnspan=2, pady=8)
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
        self.use_existing = self.mode.get() == "開いているタブ"
        if self.use_existing:
            name = self.name.get().strip()
            folder = Path(self.folder.get())
            try:
                width, height = map(int, self.size.get().lower().replace("×", "x").split("x"))
                if min(width, height) < 1:
                    raise ValueError()
            except ValueError:
                self.status.set("幅と高さを 1400x2000 の形式で入力してください。")
                return
            actions = list(self.winfo_toplevel().scanner.actions)
            command = self.command.get().strip()
            if not (name and folder.is_dir() and len(self.points) == 2 and command.isascii() and (actions or count == 1)):
                self.status.set("資料名、ダウンロード先、画面上の2点、英語コマンド、ページ送り操作を確認してください。")
                return
            task = capture_existing
            args = (name, count, folder, (width, height), self.points[0], self.points[1], command, actions)
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
        self.stop.clear()
        self.running = True
        self.start.state(["disabled"])
        self.cancel.state(["!disabled"])
        self.status.set("Chromeを準備中…")
        threading.Thread(target=self.worker, args=(task, *args), daemon=True).start()

    def worker(self, task, *args):
        try:
            path = task(self.root / "output", *args, self.confirm,
                       lambda msg: self.events.put(("status", msg, None)), self.stop)
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

    def proceed(self):
        self.next.state(["disabled"])
        if self.use_existing:
            self.winfo_toplevel().iconify()
        self.ready.set()

    def poll(self):
        try:
            while True:
                kind, msg, path = self.events.get_nowait()
                self.status.set(msg)
                if self.use_existing and kind in ("confirm", "done"):
                    self.winfo_toplevel().deiconify()
                if kind == "confirm":
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
