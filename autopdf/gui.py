from __future__ import annotations

import threading
import re
import os
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from .config import ConfigStore
from .layout_filter import OllamaContentFilter
from .models import PageTurnAction, ScanProfile, TTSSettings
from .scanner import PageTurnRecorder, ScanCancelled, ScanEngine, execute_action
from .sbv2 import ModelInfo, StyleBertVITS2Client
from .screen_picker import ScreenPicker, enable_screen_dpi_awareness
from .tts_job import PDFTTSJob, TTSCancelled


# Some Windows Python installations include Tcl/Tk but do not expose its library
# path correctly to virtual environments. Point Tk at the bundled files when found.
_tcl_root = Path(sys.base_prefix) / "tcl"
if (_tcl_root / "tcl8.6" / "init.tcl").is_file():
    os.environ.setdefault("TCL_LIBRARY", str(_tcl_root / "tcl8.6"))
if (_tcl_root / "tk8.6" / "tk.tcl").is_file():
    os.environ.setdefault("TK_LIBRARY", str(_tcl_root / "tk8.6"))


def _float(value: str, label: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{label}は数値で入力してください。") from exc


def _int(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label}は整数で入力してください。") from exc


class ScannerFrame(ttk.Frame):
    def __init__(self, master, app: "AutoPDFApp"):
        super().__init__(master, padding=10)
        self.app = app
        self.stop_event = threading.Event()
        self.running = False
        self.actions: list[PageTurnAction] = []
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        profile_box = ttk.LabelFrame(self, text="スキャン設定", padding=8)
        profile_box.grid(row=0, column=0, sticky="nsew")
        for column in (1, 3, 5, 7):
            profile_box.columnconfigure(column, weight=1)

        self.profile_name = tk.StringVar()
        self.profile_combo = ttk.Combobox(
            profile_box, textvariable=self.profile_name, state="normal", width=24
        )
        self.profile_combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=4)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_profile())
        ttk.Label(profile_box, text="プロファイル").grid(row=0, column=0, sticky="w")
        ttk.Button(profile_box, text="保存", command=self.save_profile).grid(row=0, column=3)
        ttk.Button(profile_box, text="削除", command=self.delete_profile).grid(row=0, column=4)

        self.vars: dict[str, tk.Variable] = {
            "left": tk.StringVar(),
            "top": tk.StringVar(),
            "right": tk.StringVar(),
            "bottom": tk.StringVar(),
            "page_count": tk.StringVar(),
            "start_delay": tk.StringVar(),
            "output_dir": tk.StringVar(),
            "file_prefix": tk.StringVar(),
            "pdf_name": tk.StringVar(),
            "fit_a4": tk.BooleanVar(),
            "jpeg_quality": tk.StringVar(),
            "duplicate_threshold": tk.StringVar(),
            "stop_on_duplicate": tk.BooleanVar(),
        }
        labels = [("左", "left"), ("上", "top"), ("右", "right"), ("下", "bottom")]
        for i, (label, key) in enumerate(labels):
            ttk.Label(profile_box, text=label).grid(row=1, column=i * 2, sticky="e")
            ttk.Entry(profile_box, textvariable=self.vars[key], width=8).grid(
                row=1, column=i * 2 + 1, sticky="ew", padx=(2, 8)
            )
        ttk.Button(
            profile_box,
            text="画面上で撮影範囲を選択",
            command=self.pick_capture_area,
        ).grid(row=2, column=0, columnspan=4, sticky="ew", padx=2, pady=3)

        fields = [
            ("ページ数", "page_count"),
            ("開始待機(秒)", "start_delay"),
            ("画像品質", "jpeg_quality"),
            ("重複判定(%)", "duplicate_threshold"),
        ]
        for i, (label, key) in enumerate(fields):
            ttk.Label(profile_box, text=label).grid(row=3, column=i * 2, sticky="e")
            ttk.Entry(profile_box, textvariable=self.vars[key], width=8).grid(
                row=3, column=i * 2 + 1, sticky="ew", padx=(2, 8)
            )

        ttk.Label(profile_box, text="出力フォルダ").grid(row=4, column=0, sticky="e")
        ttk.Entry(profile_box, textvariable=self.vars["output_dir"]).grid(
            row=4, column=1, columnspan=5, sticky="ew", padx=4
        )
        ttk.Button(profile_box, text="参照", command=self.choose_output).grid(row=4, column=6)
        ttk.Label(profile_box, text="PDF名").grid(row=5, column=0, sticky="e")
        ttk.Entry(profile_box, textvariable=self.vars["pdf_name"]).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=4
        )
        ttk.Label(profile_box, text="画像接頭辞").grid(row=5, column=3, sticky="e")
        ttk.Entry(profile_box, textvariable=self.vars["file_prefix"]).grid(
            row=5, column=4, columnspan=2, sticky="ew", padx=4
        )
        ttk.Checkbutton(
            profile_box, text="A4へ整形", variable=self.vars["fit_a4"]
        ).grid(row=6, column=1, sticky="w")
        ttk.Checkbutton(
            profile_box,
            text="同一ページ検出で停止",
            variable=self.vars["stop_on_duplicate"],
        ).grid(row=6, column=2, columnspan=2, sticky="w")

        action_box = ttk.LabelFrame(self, text="ページめくり手順（上から順に再生）", padding=8)
        action_box.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        action_box.columnconfigure(0, weight=1)
        action_box.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.action_tree = ttk.Treeview(
            action_box, columns=("number", "detail"), show="headings", height=7
        )
        self.action_tree.heading("number", text="#")
        self.action_tree.heading("detail", text="操作")
        self.action_tree.column("number", width=40, stretch=False, anchor="center")
        self.action_tree.column("detail", width=620)
        self.action_tree.grid(row=0, column=0, columnspan=8, sticky="nsew")
        scrollbar = ttk.Scrollbar(action_box, orient="vertical", command=self.action_tree.yview)
        scrollbar.grid(row=0, column=8, sticky="ns")
        self.action_tree.configure(yscrollcommand=scrollbar.set)
        buttons = [
            ("キー追加", self.add_key),
            ("クリック追加", self.add_click),
            ("ドラッグ追加", self.add_drag),
            ("スクロール追加", self.add_scroll),
            ("待機追加", self.add_wait),
            ("↑", lambda: self.move_action(-1)),
            ("↓", lambda: self.move_action(1)),
            ("削除", self.delete_action),
        ]
        for index, (text, command) in enumerate(buttons):
            ttk.Button(action_box, text=text, command=command).grid(
                row=1, column=index, padx=2, pady=(5, 0), sticky="ew"
            )
        ttk.Button(
            action_box, text="1回分を記録（F8で終了）", command=self.record_actions
        ).grid(row=2, column=0, columnspan=4, padx=2, pady=5, sticky="ew")
        ttk.Button(action_box, text="手順をテスト", command=self.test_actions).grid(
            row=2, column=4, columnspan=4, padx=2, pady=5, sticky="ew"
        )

        control = ttk.Frame(self)
        control.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        control.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(control, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.start_button = ttk.Button(control, text="スキャン開始", command=self.start_scan)
        self.start_button.grid(row=0, column=1, padx=2)
        self.stop_button = ttk.Button(
            control, text="停止", command=self.stop_scan, state="disabled"
        )
        self.stop_button.grid(row=0, column=2, padx=2)
        self.status = tk.StringVar(value="準備完了")
        ttk.Label(control, textvariable=self.status).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

    def refresh_profile_names(self) -> None:
        self.profile_combo["values"] = list(self.app.profiles)

    def set_profile(self, profile: ScanProfile) -> None:
        self.profile_name.set(profile.name)
        for key in (
            "left",
            "top",
            "right",
            "bottom",
            "page_count",
            "start_delay",
            "output_dir",
            "file_prefix",
            "pdf_name",
            "fit_a4",
            "jpeg_quality",
            "duplicate_threshold",
            "stop_on_duplicate",
        ):
            self.vars[key].set(getattr(profile, key))
        self.actions = [PageTurnAction(item.kind, dict(item.params)) for item in profile.actions]
        self.refresh_actions()

    def get_profile(self) -> ScanProfile:
        profile = ScanProfile(
            name=self.profile_name.get().strip() or "新規プロファイル",
            left=_int(str(self.vars["left"].get()), "左"),
            top=_int(str(self.vars["top"].get()), "上"),
            right=_int(str(self.vars["right"].get()), "右"),
            bottom=_int(str(self.vars["bottom"].get()), "下"),
            page_count=_int(str(self.vars["page_count"].get()), "ページ数"),
            start_delay=_float(str(self.vars["start_delay"].get()), "開始待機"),
            output_dir=str(self.vars["output_dir"].get()).strip() or "output",
            file_prefix=str(self.vars["file_prefix"].get()).strip() or "picture",
            pdf_name=str(self.vars["pdf_name"].get()).strip() or "scanned.pdf",
            fit_a4=bool(self.vars["fit_a4"].get()),
            jpeg_quality=_int(str(self.vars["jpeg_quality"].get()), "画像品質"),
            duplicate_threshold=_float(
                str(self.vars["duplicate_threshold"].get()), "重複判定"
            ),
            stop_on_duplicate=bool(self.vars["stop_on_duplicate"].get()),
            actions=[PageTurnAction(item.kind, dict(item.params)) for item in self.actions],
        )
        profile.validate()
        return profile

    def load_profile(self) -> None:
        profile = self.app.profiles.get(self.profile_name.get())
        if profile:
            self.set_profile(profile)

    def save_profile(self, quiet: bool = False) -> None:
        try:
            profile = self.get_profile()
            self.app.profiles[profile.name] = profile
            self.refresh_profile_names()
            self.app.save_config()
            if not quiet:
                self.status.set(f"プロファイル「{profile.name}」を保存しました。")
        except Exception as exc:
            if not quiet:
                messagebox.showerror("設定エラー", str(exc), parent=self)

    def delete_profile(self) -> None:
        name = self.profile_name.get()
        if name not in self.app.profiles:
            return
        if not messagebox.askyesno("確認", f"「{name}」を削除しますか？", parent=self):
            return
        del self.app.profiles[name]
        if not self.app.profiles:
            default = ScanProfile()
            self.app.profiles[default.name] = default
        self.refresh_profile_names()
        self.set_profile(next(iter(self.app.profiles.values())))
        self.app.save_config()

    def choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.vars["output_dir"].set(path)

    def pick_capture_area(self) -> None:
        keys = ("left", "top", "right", "bottom")
        try:
            previous = tuple(int(self.vars[key].get()) for key in keys)
        except ValueError:
            previous = None

        def apply_area(area):
            for key, coordinate in zip(keys, area):
                self.vars[key].set(coordinate)
            self.status.set(f"撮影範囲を設定しました。{area[2] - area[0]} × {area[3] - area[1]} px")

        self._pick_on_screen(point=False, previous=previous, apply_selection=apply_area)

    def _pick_on_screen(self, *, point, previous=None, apply_selection) -> None:
        if self.running:
            return
        window_state = self.app.state()
        self.app.withdraw()

        def select():
            result = None
            error = None
            try:
                picker = ScreenPicker(self.app, point=point, previous=previous)
                self.app.wait_window(picker)
                result = picker.result
            except Exception as exc:
                error = exc
            finally:
                self.app.state(window_state)
                self.app.lift()
            if error:
                messagebox.showerror("画面選択エラー", str(error), parent=self)
            elif result is not None:
                apply_selection(result)

        # Let Windows remove the settings window before grabbing the desktop.
        self.app.after(150, select)

    def refresh_actions(self) -> None:
        self.action_tree.delete(*self.action_tree.get_children())
        for index, action in enumerate(self.actions, start=1):
            self.action_tree.insert("", "end", iid=str(index - 1), values=(index, action.describe()))

    def add_key(self) -> None:
        key = simpledialog.askstring(
            "キー操作", "キー名（例: right / pagedown / space）", initialvalue="right", parent=self
        )
        if key:
            self.actions.append(PageTurnAction("key", {"key": key.strip()}))
            self.refresh_actions()

    def add_click(self) -> None:
        def apply_point(point):
            x, y = point
            self.actions.append(PageTurnAction("click", {"x": x, "y": y, "button": "left"}))
            self.refresh_actions()
            self.action_tree.selection_set(str(len(self.actions) - 1))
            self.status.set(f"クリック位置 ({x}, {y}) を追加しました。")

        self._pick_on_screen(point=True, apply_selection=apply_point)

    def add_drag(self) -> None:
        value = simpledialog.askstring(
            "ドラッグ", "x1,y1,x2,y2,秒", initialvalue="1200,540,700,540,0.3", parent=self
        )
        if value:
            try:
                x1, y1, x2, y2, duration = [item.strip() for item in value.split(",")]
                self.actions.append(
                    PageTurnAction(
                        "drag",
                        {
                            "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
                            "duration": float(duration), "button": "left",
                        },
                    )
                )
                self.refresh_actions()
            except Exception as exc:
                messagebox.showerror("入力エラー", str(exc), parent=self)

    def add_scroll(self) -> None:
        value = simpledialog.askinteger(
            "スクロール", "量（下方向は負数）", initialvalue=-3, parent=self
        )
        if value is not None:
            self.actions.append(PageTurnAction("scroll", {"clicks": value}))
            self.refresh_actions()

    def add_wait(self) -> None:
        value = simpledialog.askfloat("待機", "秒数", initialvalue=0.3, minvalue=0.0, parent=self)
        if value is not None:
            self.actions.append(PageTurnAction("wait", {"seconds": value}))
            self.refresh_actions()

    def _selected_action_index(self) -> int | None:
        selected = self.action_tree.selection()
        return int(selected[0]) if selected else None

    def delete_action(self) -> None:
        index = self._selected_action_index()
        if index is not None:
            self.actions.pop(index)
            self.refresh_actions()

    def move_action(self, delta: int) -> None:
        index = self._selected_action_index()
        if index is None:
            return
        target = index + delta
        if 0 <= target < len(self.actions):
            self.actions[index], self.actions[target] = self.actions[target], self.actions[index]
            self.refresh_actions()
            self.action_tree.selection_set(str(target))

    def record_actions(self) -> None:
        if self.running:
            return
        if not messagebox.askokcancel(
            "ページめくり記録",
            "OKの2秒後から記録します。文書画面で1回だけページをめくり、F8キーで終了してください。\n"
            "実際に画面へ入力されます。既存の手順は記録結果で置き換えます。",
            parent=self,
        ):
            return
        self.running = True
        self.status.set("2秒後に記録開始。1回ページをめくってF8を押してください。")

        def worker():
            try:
                actions = PageTurnRecorder().record()
                self.after(0, lambda: self._record_finished(actions, None))
            except Exception as exc:
                self.after(0, lambda error=exc: self._record_finished([], error))

        threading.Thread(target=worker, daemon=True).start()

    def _record_finished(self, actions: list[PageTurnAction], error: Exception | None) -> None:
        self.running = False
        if error:
            messagebox.showerror("記録エラー", str(error), parent=self)
            self.status.set("記録に失敗しました。")
            return
        self.actions = actions
        self.refresh_actions()
        self.status.set(f"{len(actions)}操作を記録しました。手順をテストしてください。")

    def test_actions(self) -> None:
        if self.running or not self.actions:
            return
        if not messagebox.askokcancel(
            "手順テスト", "OKの2秒後に、現在のページめくり手順を1回再生します。", parent=self
        ):
            return
        self.running = True
        stop = threading.Event()

        def worker():
            try:
                if stop.wait(2):
                    return
                for action in self.actions:
                    execute_action(action, stop)
                self.after(0, lambda: self.status.set("手順テストが完了しました。"))
            except Exception as exc:
                self.after(
                    0,
                    lambda error=exc: messagebox.showerror(
                        "テストエラー", str(error), parent=self
                    ),
                )
            finally:
                self.after(0, lambda: setattr(self, "running", False))

        threading.Thread(target=worker, daemon=True).start()

    def start_scan(self) -> None:
        if self.running:
            return
        try:
            profile = self.get_profile()
            self.app.profiles[profile.name] = profile
            self.app.save_config()
        except Exception as exc:
            messagebox.showerror("設定エラー", str(exc), parent=self)
            return
        self.stop_event.clear()
        self.running = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.configure(value=0, maximum=profile.page_count)

        def progress(message: str, done: int, total: int):
            self.after(0, lambda: self._scan_progress(message, done, total))

        def worker():
            try:
                path = ScanEngine(self.app.project_root).run(profile, self.stop_event, progress)
                self.after(0, lambda: self._scan_finished(path, None))
            except ScanCancelled:
                self.after(0, lambda: self._scan_finished(None, None))
            except Exception as exc:
                self.after(0, lambda error=exc: self._scan_finished(None, error))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_progress(self, message: str, done: int, total: int) -> None:
        self.status.set(f"{message} ({done}/{total})")
        self.progress.configure(maximum=max(1, total), value=done)

    def _scan_finished(self, path: Path | None, error: Exception | None) -> None:
        self.running = False
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        if error:
            messagebox.showerror("スキャンエラー", str(error), parent=self)
            self.status.set("エラーで終了しました。")
        elif path:
            self.status.set(f"完了: {path}")
            messagebox.showinfo("スキャン完了", f"PDFを作成しました。\n{path}", parent=self)
        else:
            self.status.set("停止しました。撮影済み画像は出力フォルダに残っています。")

    def stop_scan(self) -> None:
        self.stop_event.set()
        self.status.set("停止を要求しました…")


class TTSFrame(ttk.Frame):
    def __init__(self, master, app: "AutoPDFApp", initial: TTSSettings, ui: dict):
        super().__init__(master, padding=10)
        self.app = app
        self.stop_event = threading.Event()
        self.running = False
        self.models: list[ModelInfo] = []
        self.preview_cache: tuple[tuple, dict[int, str]] | None = None
        self.saved_settings = initial
        self._build(initial, ui)

    def _build(self, initial: TTSSettings, ui: dict) -> None:
        self.columnconfigure(0, weight=1)
        source = ttk.LabelFrame(self, text="PDFと文字抽出", padding=8)
        source.grid(row=0, column=0, sticky="ew")
        source.columnconfigure(1, weight=1)
        self.pdf_path = tk.StringVar(value=ui.get("last_pdf", ""))
        self.output_dir = tk.StringVar(value=ui.get("tts_output_dir", "output"))
        self.page_range = tk.StringVar(value=ui.get("page_range", ""))
        self.extract_mode = tk.StringVar(value=ui.get("extract_mode", "auto"))
        self.tesseract_cmd = tk.StringVar(value=ui.get("tesseract_cmd", ""))
        self.ocr_language = tk.StringVar(value=ui.get("ocr_language", "jpn"))
        self.exclude_tables = tk.BooleanVar(value=ui.get("exclude_tables", True))
        self.exclude_images = tk.BooleanVar(value=ui.get("exclude_images", True))
        self.exclude_captions = tk.BooleanVar(value=ui.get("exclude_captions", True))
        self.use_ollama = tk.BooleanVar(value=ui.get("use_ollama", False))
        self.ollama_url = tk.StringVar(
            value=ui.get("ollama_url", "http://127.0.0.1:11434")
        )
        self.ollama_model = tk.StringVar(value=ui.get("ollama_model") or "qwen3.5:2b")
        rows = [
            ("PDF", self.pdf_path, self.choose_pdf),
            ("出力フォルダ", self.output_dir, self.choose_output),
        ]
        for row, (label, variable, command) in enumerate(rows):
            ttk.Label(source, text=label).grid(row=row, column=0, sticky="e")
            ttk.Entry(source, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Button(source, text="参照", command=command).grid(row=row, column=2)
        ttk.Label(source, text="ページ範囲").grid(row=2, column=0, sticky="e")
        ttk.Entry(source, textvariable=self.page_range, width=18).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(source, text="例: 1-20,25（空欄=全ページ）").grid(row=2, column=1, sticky="e")
        ttk.Label(source, text="抽出").grid(row=3, column=0, sticky="e")
        ttk.Combobox(
            source,
            textvariable=self.extract_mode,
            values=("auto", "text", "ocr"),
            state="readonly",
            width=10,
        ).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Label(source, text="OCR言語").grid(row=3, column=1, sticky="e", padx=(0, 175))
        ttk.Entry(source, textvariable=self.ocr_language, width=14).grid(row=3, column=1, sticky="e", padx=(0, 45))
        ttk.Label(source, text="Tesseract").grid(row=4, column=0, sticky="e")
        ttk.Entry(source, textvariable=self.tesseract_cmd).grid(row=4, column=1, sticky="ew", padx=4)
        ttk.Button(source, text="exe参照", command=self.choose_tesseract).grid(row=4, column=2)
        filters = ttk.Frame(source)
        filters.grid(row=5, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(filters, text="読み上げ除外:").pack(side="left")
        ttk.Checkbutton(filters, text="表", variable=self.exclude_tables).pack(side="left")
        ttk.Checkbutton(
            filters, text="画像・図内文字", variable=self.exclude_images
        ).pack(side="left")
        ttk.Checkbutton(
            filters, text="図表キャプション", variable=self.exclude_captions
        ).pack(side="left")
        ttk.Checkbutton(
            filters, text="難しいページだけQwenで判定", variable=self.use_ollama
        ).pack(side="left", padx=(12, 0))
        ttk.Label(source, text="Ollama").grid(row=6, column=0, sticky="e")
        ollama_line = ttk.Frame(source)
        ollama_line.grid(row=6, column=1, sticky="ew", padx=4)
        ollama_line.columnconfigure(0, weight=2)
        ollama_line.columnconfigure(1, weight=1)
        ttk.Entry(ollama_line, textvariable=self.ollama_url).grid(
            row=0, column=0, sticky="ew"
        )
        self.ollama_model_combo = ttk.Combobox(
            ollama_line, textvariable=self.ollama_model, state="normal", width=22
        )
        self.ollama_model_combo.grid(row=0, column=1, sticky="ew", padx=(5, 0))
        ttk.Button(source, text="モデル取得", command=self.load_ollama_models).grid(
            row=6, column=2
        )

        api = ttk.LabelFrame(self, text="Style-Bert-VITS2", padding=8)
        api.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in (1, 3, 5):
            api.columnconfigure(column, weight=1)
        self.api_url = tk.StringVar(value=initial.api_url)
        self.model_value = tk.StringVar()
        self.speaker_value = tk.StringVar()
        self.style_value = tk.StringVar(value=initial.style)
        self.model_id = initial.model_id
        ttk.Label(api, text="API URL").grid(row=0, column=0, sticky="e")
        ttk.Entry(api, textvariable=self.api_url).grid(row=0, column=1, columnspan=4, sticky="ew", padx=4)
        ttk.Button(api, text="接続・モデル取得", command=self.load_models).grid(row=0, column=5, sticky="ew")
        ttk.Label(api, text="モデル").grid(row=1, column=0, sticky="e")
        self.model_combo = ttk.Combobox(api, textvariable=self.model_value, state="readonly")
        self.model_combo.grid(row=1, column=1, sticky="ew", padx=4)
        self.model_combo.bind("<<ComboboxSelected>>", lambda _event: self.model_changed())
        ttk.Label(api, text="話者").grid(row=1, column=2, sticky="e")
        self.speaker_combo = ttk.Combobox(api, textvariable=self.speaker_value, state="readonly")
        self.speaker_combo.grid(row=1, column=3, sticky="ew", padx=4)
        ttk.Label(api, text="スタイル").grid(row=1, column=4, sticky="e")
        self.style_combo = ttk.Combobox(api, textvariable=self.style_value, state="normal")
        self.style_combo.grid(row=1, column=5, sticky="ew", padx=4)

        self.option_vars = {
            "style_weight": tk.StringVar(value=str(initial.style_weight)),
            "length": tk.StringVar(value=str(initial.length)),
            "sdp_ratio": tk.StringVar(value=str(initial.sdp_ratio)),
            "noise": tk.StringVar(value=str(initial.noise)),
            "noisew": tk.StringVar(value=str(initial.noisew)),
            "max_chars": tk.StringVar(value=str(initial.max_chars)),
            "silence_ms": tk.StringVar(value=str(initial.silence_ms)),
        }
        option_labels = [
            ("スタイル強度", "style_weight"), ("話速", "length"),
            ("SDP比率", "sdp_ratio"), ("ノイズ", "noise"),
            ("SDPノイズ", "noisew"), ("文字/チャンク", "max_chars"),
            ("無音ms", "silence_ms"),
        ]
        for index, (label, key) in enumerate(option_labels):
            row = 2 + index // 4
            col = (index % 4) * 2
            ttk.Label(api, text=label).grid(row=row, column=col, sticky="e")
            ttk.Entry(api, textvariable=self.option_vars[key], width=9).grid(
                row=row, column=col + 1, sticky="ew", padx=(2, 8)
            )

        preview_box = ttk.LabelFrame(self, text="抽出テキストの確認・修正", padding=8)
        preview_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        preview_box.columnconfigure(0, weight=1)
        preview_box.rowconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.preview = ScrolledText(preview_box, wrap="word", height=12, font=("Yu Gothic UI", 10))
        self.preview.grid(row=0, column=0, sticky="nsew")

        control = ttk.Frame(self)
        control.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        control.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(control)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.analyze_button = ttk.Button(control, text="PDF解析", command=self.analyze)
        self.analyze_button.grid(row=0, column=1, padx=2)
        self.start_button = ttk.Button(control, text="音声合成開始", command=self.start_tts)
        self.start_button.grid(row=0, column=2, padx=2)
        self.stop_button = ttk.Button(control, text="停止", command=self.stop, state="disabled")
        self.stop_button.grid(row=0, column=3, padx=2)
        self.status = tk.StringVar(value="PDFを選び、まず解析してください。")
        ttk.Label(control, textvariable=self.status).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

    def choose_pdf(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")], parent=self)
        if path:
            self.pdf_path.set(path)
            if self.output_dir.get() == "output":
                self.output_dir.set(str(Path(path).parent / "output"))

    def choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.output_dir.set(path)

    def choose_tesseract(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Tesseract", "tesseract.exe"), ("EXE", "*.exe")], parent=self)
        if path:
            self.tesseract_cmd.set(path)

    def current_settings(self) -> TTSSettings:
        model = self._selected_model()
        speaker_name = self.speaker_value.get().strip()
        speaker_id = model.speakers.get(speaker_name, 0) if model else 0
        settings = TTSSettings(
            api_url=self.api_url.get().strip(),
            model_name=model.model_name if model else "",
            model_id=model.model_id if model else self.model_id,
            speaker_name=speaker_name,
            speaker_id=speaker_id,
            style=self.style_value.get().strip() or "Neutral",
            style_weight=_float(self.option_vars["style_weight"].get(), "スタイル強度"),
            length=_float(self.option_vars["length"].get(), "話速"),
            sdp_ratio=_float(self.option_vars["sdp_ratio"].get(), "SDP比率"),
            noise=_float(self.option_vars["noise"].get(), "ノイズ"),
            noisew=_float(self.option_vars["noisew"].get(), "SDPノイズ"),
            max_chars=_int(self.option_vars["max_chars"].get(), "文字/チャンク"),
            silence_ms=_int(self.option_vars["silence_ms"].get(), "無音ms"),
        )
        settings.validate()
        return settings

    def ui_settings(self) -> dict:
        return {
            "last_pdf": self.pdf_path.get(), "tts_output_dir": self.output_dir.get(),
            "page_range": self.page_range.get(), "extract_mode": self.extract_mode.get(),
            "tesseract_cmd": self.tesseract_cmd.get(), "ocr_language": self.ocr_language.get(),
            "exclude_tables": self.exclude_tables.get(),
            "exclude_images": self.exclude_images.get(),
            "exclude_captions": self.exclude_captions.get(),
            "use_ollama": self.use_ollama.get(),
            "ollama_url": self.ollama_url.get(),
            "ollama_model": self.ollama_model.get(),
        }

    def load_ollama_models(self) -> None:
        if self.running:
            return
        self.running = True
        self.status.set("Ollamaへ接続しています…")

        def worker():
            try:
                models = OllamaContentFilter(
                    self.ollama_url.get().strip(), timeout=15
                ).list_models()
                self.after(0, lambda: self._ollama_models_loaded(models, None))
            except Exception as exc:
                self.after(
                    0, lambda error=exc: self._ollama_models_loaded([], error)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _ollama_models_loaded(
        self, models: list[str], error: Exception | None
    ) -> None:
        self.running = False
        if error:
            messagebox.showerror("Ollama接続エラー", str(error), parent=self)
            self.status.set("Ollamaへ接続できませんでした。")
            return
        self.ollama_model_combo["values"] = models
        if models and not self.ollama_model.get():
            self.ollama_model.set(models[0])
        self.status.set(f"Ollamaから{len(models)}個のモデルを取得しました。")

    def _selected_model(self) -> ModelInfo | None:
        index = self.model_combo.current()
        return self.models[index] if 0 <= index < len(self.models) else None

    def load_models(self) -> None:
        if self.running:
            return
        self.status.set("Style-Bert-VITS2へ接続しています…")
        self.running = True

        def worker():
            try:
                models = StyleBertVITS2Client(self.api_url.get().strip(), timeout=15).get_models()
                self.after(0, lambda: self._models_loaded(models, None))
            except Exception as exc:
                self.after(0, lambda error=exc: self._models_loaded([], error))

        threading.Thread(target=worker, daemon=True).start()

    def _models_loaded(self, models: list[ModelInfo], error: Exception | None) -> None:
        self.running = False
        if error:
            messagebox.showerror("API接続エラー", str(error), parent=self)
            self.status.set("APIへ接続できませんでした。server_fastapi.pyを確認してください。")
            return
        self.models = models
        self.model_combo["values"] = [f"{item.model_id}: {item.model_name}" for item in models]
        if models:
            selected = next(
                (
                    index
                    for index, item in enumerate(models)
                    if item.model_name == self.saved_settings.model_name
                    or item.model_id == self.saved_settings.model_id
                ),
                0,
            )
            self.model_combo.current(selected)
            self.model_changed()
            model = self._selected_model()
            if model and self.saved_settings.speaker_name in model.speakers:
                self.speaker_combo.set(self.saved_settings.speaker_name)
            if model and self.saved_settings.style in model.styles:
                self.style_combo.set(self.saved_settings.style)
        self.status.set(f"{len(models)}個のモデルを取得しました。")

    def model_changed(self) -> None:
        model = self._selected_model()
        if not model:
            return
        speakers = list(model.speakers) or [""]
        styles = list(model.styles) or ["Neutral"]
        self.speaker_combo["values"] = speakers
        self.speaker_combo.current(0)
        self.style_combo["values"] = styles
        self.style_combo.set(styles[0])

    def _job(self) -> PDFTTSJob:
        pdf = Path(self.pdf_path.get().strip())
        if not pdf.is_file():
            raise ValueError("読み込むPDFを選択してください。")
        if self.use_ollama.get() and not self.ollama_model.get().strip():
            raise ValueError(
                "Ollama本文判定を使う場合は、Ollamaモデルを選択してください。"
            )
        output = Path(self.output_dir.get().strip() or "output")
        return PDFTTSJob(
            pdf, output, self.page_range.get(), self.current_settings(),
            extraction_mode=self.extract_mode.get(),
            tesseract_cmd=self.tesseract_cmd.get(),
            ocr_language=self.ocr_language.get(),
            exclude_tables=self.exclude_tables.get(),
            exclude_images=self.exclude_images.get(),
            exclude_captions=self.exclude_captions.get(),
            use_ollama=self.use_ollama.get(),
            ollama_url=self.ollama_url.get().strip(),
            ollama_model=self.ollama_model.get().strip(),
        )

    def _cache_key(self) -> tuple:
        return (
            self.pdf_path.get(), self.page_range.get(), self.extract_mode.get(),
            self.tesseract_cmd.get(), self.ocr_language.get(),
            self.exclude_tables.get(), self.exclude_images.get(),
            self.exclude_captions.get(), self.use_ollama.get(),
            self.ollama_url.get(), self.ollama_model.get(),
        )

    def analyze(self) -> None:
        if self.running:
            return
        try:
            job = self._job()
        except Exception as exc:
            messagebox.showerror("設定エラー", str(exc), parent=self)
            return
        self.running = True
        self.stop_event.clear()
        self._set_busy(True)
        self.status.set("PDFを解析しています…")

        def progress(done, total, message):
            self.after(0, lambda: self._progress("文字抽出: " + message, done, total))

        def worker():
            try:
                texts = job.extract_texts(progress, stop_event=self.stop_event)
                self.after(0, lambda: self._analysis_finished(texts, None))
            except TTSCancelled:
                self.after(0, lambda: self._analysis_finished({}, TTSCancelled()))
            except Exception as exc:
                self.after(0, lambda error=exc: self._analysis_finished({}, error))

        threading.Thread(target=worker, daemon=True).start()

    def _analysis_finished(self, texts: dict[int, str], error: Exception | None) -> None:
        self.running = False
        self._set_busy(False)
        if isinstance(error, TTSCancelled):
            self.status.set("PDF解析を停止しました。")
            return
        if error:
            messagebox.showerror("PDF解析エラー", str(error), parent=self)
            self.status.set("解析に失敗しました。")
            return
        self.preview_cache = (self._cache_key(), texts)
        self.preview.delete("1.0", "end")
        for page, text in texts.items():
            self.preview.insert("end", f"=== {page + 1}ページ ===\n{text}\n\n")
        count = sum(len(text) for text in texts.values())
        self.status.set(f"{len(texts)}ページ、{count:,}文字を抽出しました。内容を確認してください。")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.analyze_button.configure(state=state)
        self.start_button.configure(state=state)
        self.stop_button.configure(state="normal" if busy else "disabled")

    def start_tts(self) -> None:
        if self.running:
            return
        try:
            job = self._job()
            if not self.models:
                raise ValueError("先に「接続・モデル取得」でモデルと話者を確認してください。")
        except Exception as exc:
            messagebox.showerror("設定エラー", str(exc), parent=self)
            return
        cached = None
        if self.preview_cache and self.preview_cache[0] == self._cache_key():
            cached = self._edited_preview_texts() or self.preview_cache[1]
        self.stop_event.clear()
        self.running = True
        self._set_busy(True)

        def progress(message, done, total):
            self.after(0, lambda: self._progress(message, done, total))

        def worker():
            try:
                output = job.run(self.stop_event, progress, pre_extracted=cached)
                self.after(0, lambda: self._tts_finished(output, None))
            except TTSCancelled:
                self.after(0, lambda: self._tts_finished(None, None))
            except Exception as exc:
                self.after(0, lambda error=exc: self._tts_finished(None, error))

        threading.Thread(target=worker, daemon=True).start()

    def _edited_preview_texts(self) -> dict[int, str]:
        content = self.preview.get("1.0", "end-1c")
        matches = list(re.finditer(r"^===\s*(\d+)ページ\s*===\s*$", content, re.MULTILINE))
        result: dict[int, str] = {}
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            result[int(match.group(1)) - 1] = content[start:end].strip()
        return result

    def _progress(self, message: str, done: int, total: int) -> None:
        self.status.set(f"{message} ({done}/{total})")
        self.progress.configure(maximum=max(1, total), value=done)

    def _tts_finished(self, output: Path | None, error: Exception | None) -> None:
        self.running = False
        self._set_busy(False)
        if error:
            messagebox.showerror("音声合成エラー", str(error), parent=self)
            self.status.set("音声合成に失敗しました。作成済みチャンクは残っています。")
        elif output:
            stem = output.name.removesuffix("_audiobook.wav")
            package = output.parent / f"{stem}.autobook"
            self.status.set(f"完了: {output} / {package.name}")
            messagebox.showinfo(
                "音声合成完了",
                f"WAVとAndroid転送用AutoBookを作成しました。\n{output}\n{package}",
                parent=self,
            )
        else:
            self.status.set("停止しました。作成済みチャンクは出力フォルダに残っています。")

    def stop(self) -> None:
        self.stop_event.set()
        self.status.set("停止を要求しました。現在のAPI応答後に停止します…")


class AutoPDFApp(tk.Tk):
    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root
        self.title("AutoPDF 2 - スキャン / PDF読み上げ")
        self.geometry("980x820")
        self.minsize(820, 720)
        self.store = ConfigStore(project_root / "autopdf_settings.json")
        try:
            self.profiles, tts, ui = self.store.load()
        except ValueError as exc:
            messagebox.showwarning("設定読込エラー", str(exc), parent=self)
            default = ScanProfile()
            self.profiles, tts, ui = {default.name: default}, TTSSettings(), {}

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)
        self.scanner = ScannerFrame(notebook, self)
        self.tts = TTSFrame(notebook, self, tts, ui)
        notebook.add(self.scanner, text="  スキャン→PDF  ")
        notebook.add(self.tts, text="  PDF→Style-Bert-VITS2  ")
        self.scanner.refresh_profile_names()
        selected = ui.get("scan_profile")
        profile = self.profiles.get(selected) or next(iter(self.profiles.values()))
        self.scanner.set_profile(profile)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def save_config(self) -> None:
        try:
            tts = self.tts.current_settings()
        except Exception:
            tts = TTSSettings(api_url=self.tts.api_url.get().strip() or "http://127.0.0.1:5000")
        ui = self.tts.ui_settings()
        ui["scan_profile"] = self.scanner.profile_name.get()
        self.store.save(self.profiles, tts, ui)

    def on_close(self) -> None:
        if self.scanner.running or self.tts.running:
            if not messagebox.askyesno(
                "処理中", "処理中です。停止を要求して終了しますか？", parent=self
            ):
                return
            self.scanner.stop_event.set()
            self.tts.stop_event.set()
        try:
            profile = self.scanner.get_profile()
            self.profiles[profile.name] = profile
            self.save_config()
        except Exception:
            pass
        self.destroy()


def launch(project_root: Path | None = None) -> None:
    enable_screen_dpi_awareness()
    root = Path(project_root or Path.cwd()).resolve()
    AutoPDFApp(root).mainloop()

