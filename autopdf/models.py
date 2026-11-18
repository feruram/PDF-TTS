from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PageTurnAction:
    """One replayable operation used to turn a document page."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PageTurnAction":
        return cls(kind=str(value["kind"]), params=dict(value.get("params", {})))

    def describe(self) -> str:
        p = self.params
        if self.kind == "key":
            return f"キー: {p.get('key', '')}"
        if self.kind == "click":
            return f"クリック: ({p.get('x')}, {p.get('y')}) {p.get('button', 'left')}"
        if self.kind == "double_click":
            return f"ダブルクリック: ({p.get('x')}, {p.get('y')})"
        if self.kind == "drag":
            return (
                f"ドラッグ: ({p.get('x1')}, {p.get('y1')}) → "
                f"({p.get('x2')}, {p.get('y2')}) / {p.get('duration', 0.3)}秒"
            )
        if self.kind == "scroll":
            return f"スクロール: {p.get('clicks', 0)}"
        if self.kind == "wait":
            return f"待機: {p.get('seconds', 0)}秒"
        return f"{self.kind}: {p}"


@dataclass
class ScanProfile:
    name: str = "Kindle - 右矢印"
    left: int = 540
    top: int = 0
    right: int = 1380
    bottom: int = 1080
    page_count: int = 10
    start_delay: float = 5.0
    output_dir: str = "output"
    file_prefix: str = "picture"
    pdf_name: str = "scanned.pdf"
    fit_a4: bool = True
    a4_width: int = 2480
    a4_height: int = 3508
    jpeg_quality: int = 92
    duplicate_threshold: float = 0.35
    stop_on_duplicate: bool = True
    actions: list[PageTurnAction] = field(
        default_factory=lambda: [
            PageTurnAction("key", {"key": "right"}),
            PageTurnAction("wait", {"seconds": 0.2}),
        ]
    )

    def validate(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("撮影範囲は右下が左上より大きくなるように指定してください。")
        if self.page_count < 1:
            raise ValueError("ページ数は1以上にしてください。")
        if self.start_delay < 0:
            raise ValueError("開始待機は0以上にしてください。")
        if not self.pdf_name.lower().endswith(".pdf"):
            raise ValueError("PDF名の末尾は .pdf にしてください。")
        if not self.actions and self.page_count > 1:
            raise ValueError("2ページ以上撮影する場合はページめくり手順が必要です。")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["actions"] = [item.to_dict() for item in self.actions]
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScanProfile":
        fields = dict(value)
        fields["actions"] = [PageTurnAction.from_dict(item) for item in fields.get("actions", [])]
        return cls(**fields)

    def output_path(self, project_root: Path) -> Path:
        path = Path(self.output_dir).expanduser()
        return path if path.is_absolute() else project_root / path


@dataclass
class TTSSettings:
    api_url: str = "http://127.0.0.1:5000"
    model_name: str = ""
    model_id: int = 0
    speaker_name: str = ""
    speaker_id: int = 0
    style: str = "Neutral"
    style_weight: float = 1.0
    length: float = 1.0
    sdp_ratio: float = 0.2
    noise: float = 0.6
    noisew: float = 0.8
    language: str = "JP"
    max_chars: int = 90
    silence_ms: int = 250

    def validate(self) -> None:
        if not self.api_url.startswith(("http://", "https://")):
            raise ValueError("API URLは http:// または https:// で始めてください。")
        if not 1 <= self.max_chars <= 10000:
            raise ValueError("1チャンクの文字数は1～10000で指定してください。")
        if self.silence_ms < 0:
            raise ValueError("チャンク間の無音は0以上にしてください。")

