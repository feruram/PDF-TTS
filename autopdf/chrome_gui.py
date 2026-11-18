import os
import threading
from queue import Empty, Queue
from tkinter import StringVar, ttk

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
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=4)
            field = (ttk.Combobox(self, textvariable=var, state="readonly",
                                  values=("Right", "Left", "PageDown", "click"))
                     if var is self.key else ttk.Entry(self, textvariable=var))
            field.grid(row=row, column=1, sticky="ew", padx=8)
        bar = ttk.Frame(self)
        bar.grid(row=4, columnspan=2, sticky="w", pady=10)
        self.start = ttk.Button(bar, text="スキャン開始", command=self.begin)
        self.next = ttk.Button(bar, text="続行", command=self.proceed, state="disabled")
        self.cancel = ttk.Button(bar, text="中断", command=self.stop.set, state="disabled")
        for button in (self.start, self.next, self.cancel):
            button.pack(side="left", padx=3)
        self.status = StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=760).grid(row=5, columnspan=2, sticky="w")
        self.preview = ttk.Label(self)
        self.preview.grid(row=6, columnspan=2, pady=8)
        self.after(100, self.poll)

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
        self.preview.configure(image="")
        self.preview.unbind("<Button-1>")
        self.stop.clear()
        self.running = True
        self.start.state(["disabled"])
        self.cancel.state(["!disabled"])
        self.status.set("Chromeを準備中…")
        threading.Thread(target=self.worker, args=(url, count, key, position), daemon=True).start()

    def worker(self, *args):
        try:
            path = run(self.root / "output", *args, self.confirm,
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
        self.ready.set()

    def poll(self):
        try:
            while True:
                kind, msg, path = self.events.get_nowait()
                self.status.set(msg)
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
