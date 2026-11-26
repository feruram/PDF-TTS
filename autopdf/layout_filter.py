from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


Box = tuple[int, int, int, int]


def detect_table_regions(image: Image.Image) -> list[Box]:
    """Detect ruled tables with OpenCV and return pixel bounding boxes.

    The detector intentionally requires several horizontal/vertical intersections,
    so a page border or an underline is not normally treated as a table.
    """

    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "表領域の検出には opencv-python-headless が必要です。"
        ) from exc

    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        13,
    )
    height, width = gray.shape
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(18, width // 35), 1)
    )
    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(18, height // 35))
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    grid = cv2.bitwise_or(horizontal, vertical)
    intersections = cv2.bitwise_and(horizontal, vertical)
    grid = cv2.dilate(grid, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    contours, _ = cv2.findContours(grid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions: list[Box] = []
    page_area = width * height
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area_ratio = (w * h) / page_area
        # Very large networks are commonly page frames, covers, or illustrations.
        # Leave those to the optional vision model instead of blanking a whole page.
        if (
            area_ratio < 0.003
            or area_ratio > 0.75
            or w < width * 0.12
            or h < height * 0.04
        ):
            continue
        roi = intersections[y : y + h, x : x + w]
        count, _labels = cv2.connectedComponents((roi > 0).astype("uint8"))
        if count - 1 < 4:
            continue
        padding = max(4, round(min(width, height) * 0.004))
        regions.append(
            (
                max(0, x - padding),
                max(0, y - padding),
                min(width, x + w + padding),
                min(height, y + h + padding),
            )
        )
    return _merge_overlapping(regions)


def _merge_overlapping(boxes: list[Box]) -> list[Box]:
    pending = list(boxes)
    merged: list[Box] = []
    while pending:
        current = pending.pop()
        changed = True
        while changed:
            changed = False
            remainder: list[Box] = []
            for other in pending:
                if _boxes_touch(current, other):
                    current = (
                        min(current[0], other[0]),
                        min(current[1], other[1]),
                        max(current[2], other[2]),
                        max(current[3], other[3]),
                    )
                    changed = True
                else:
                    remainder.append(other)
            pending = remainder
        merged.append(current)
    return sorted(merged, key=lambda item: (item[1], item[0]))


def _boxes_touch(a: Box, b: Box, gap: int = 8) -> bool:
    return not (
        a[2] + gap < b[0]
        or b[2] + gap < a[0]
        or a[3] + gap < b[1]
        or b[3] + gap < a[1]
    )


def mask_regions(image: Image.Image, regions: list[Box]) -> Image.Image:
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    for region in regions:
        draw.rectangle(region, fill="white")
    return result


CAPTION_PATTERN = re.compile(
    r"^\s*(?:図|表|写真|画像|Figure|Fig\.?|Table|Chart)\s*"
    r"(?:[0-9０-９一二三四五六七八九十]+|[:：.-])",
    re.IGNORECASE,
)


def remove_caption_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not CAPTION_PATTERN.match(line)
    ).strip()


class OllamaContentFilter:
    """Extractive body-text filter using Ollama's local structured-output API."""

    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "body_text": {"type": "string"},
            "removed_categories": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["body_text", "removed_categories"],
    }

    SYSTEM_PROMPT = """あなたは文書読み上げ前処理器です。
入力ページから連続した本文と見出しだけを、元の文字を変更せず読書順に返してください。
表のセル、図・写真・グラフ内の文字、数式、コード、キャプション、ヘッダー、フッター、
ページ番号、注釈は削除してください。要約、言い換え、OCR訂正、説明、文字の追加は禁止です。
本文がなければ body_text は空文字にしてください。"""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "",
        timeout: float = 180.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = timeout

    def _request(self, path: str, payload: dict | None = None) -> dict[str, Any]:
        data = None
        headers = {"User-Agent": "AutoPDF/2.0"}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama APIエラー ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollamaへ接続できません: {self.base_url} ({exc.reason})"
            ) from exc

    def list_models(self) -> list[str]:
        response = self._request("/api/tags")
        return [str(item.get("name", "")) for item in response.get("models", []) if item.get("name")]

    def filter_body_text(
        self, source_text: str, page_image: Image.Image | None = None
    ) -> tuple[str, list[str]]:
        if not self.model:
            raise ValueError("Ollamaモデル名を指定してください。")
        content = "次の抽出テキストから、表題・列見出し・表内の行を取り除き、本文を返してください。\n" + source_text
        message: dict[str, Any] = {"role": "user", "content": content}
        if page_image is not None:
            buffer = io.BytesIO()
            image = page_image.convert("RGB")
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            image.save(buffer, "JPEG", quality=88)
            message["images"] = [base64.b64encode(buffer.getvalue()).decode("ascii")]
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.SYSTEM_PROMPT}, message],
            "stream": False,
            "format": self.RESPONSE_SCHEMA,
            "options": {"temperature": 0},
        }
        if self.model == "qwen3.5:2b":
            payload["think"] = False
        response = self._request("/api/chat", payload)
        raw_content = response.get("message", {}).get("content", "")
        try:
            structured = json.loads(raw_content)
            body = str(structured.get("body_text", "")).strip()
            removed = [str(item) for item in structured.get("removed_categories", [])]
        except (json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise RuntimeError("Ollamaの構造化出力を解析できません。") from exc
        if body and not _is_extractive_result(source_text, body):
            raise RuntimeError(
                "Ollama出力に原文にない文字列が多いため、安全のため採用しませんでした。"
            )
        return body, removed


def _is_extractive_result(source: str, result: str) -> bool:
    source_flat = re.sub(r"\s+", "", source)
    result_flat = re.sub(r"\s+", "", result)
    if not result_flat:
        return True
    pieces = [
        re.sub(r"\s+", "", piece)
        for piece in re.split(r"(?<=[。！？!?])|\n+", result)
        if piece.strip()
    ]
    checked = sum(len(piece) for piece in pieces)
    matched = sum(len(piece) for piece in pieces if piece in source_flat)
    if checked == 0:
        return False
    return matched / checked >= 0.8


def save_debug_overlay(image: Image.Image, regions: list[Box], path: Path) -> None:
    overlay = image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    for box in regions:
        draw.rectangle(box, outline="red", width=5)
    path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(path)

