from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .ocr_setup import DATA, LANGUAGES

from .layout_filter import (
    OllamaContentFilter,
    detect_table_regions,
    mask_regions,
    remove_caption_lines,
)


def parse_page_range(value: str, page_count: int) -> list[int]:
    """Parse one-based page ranges such as ``1-3,5,8-`` to zero-based indices."""

    text = value.strip()
    if not text:
        return list(range(page_count))
    result: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            start = int(left) if left else 1
            end = int(right) if right else page_count
            if start > end:
                raise ValueError(f"ページ範囲の大小が逆です: {token}")
            result.update(range(start - 1, end))
        else:
            result.add(int(token) - 1)
    if not result or min(result) < 0 or max(result) >= page_count:
        raise ValueError(f"ページ範囲は1～{page_count}で指定してください。")
    return sorted(result)


def clean_extracted_text(text: str) -> str:
    value = text.replace("\u3000", " ").replace("\r", "\n")
    value = re.sub(r"(?<![。！？!?])\n(?!\n)", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def chunk_text(text: str, max_chars: int = 90) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_charsは1以上にしてください。")
    normalized = clean_extracted_text(text)
    if not normalized:
        return []
    sentences = re.split(r"(?<=[。！？!?])|\n+", normalized)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            split_at = max(
                sentence.rfind(mark, 0, max_chars + 1)
                for mark in ("、", "，", ",", " ")
            )
            if split_at < max_chars // 3:
                split_at = max_chars
            else:
                split_at += 1
            chunks.append(sentence[:split_at].strip())
            sentence = sentence[split_at:].strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


def _render_pdfium_page(document, page_index: int, scale: float):
    page = document[page_index]
    bitmap = None
    try:
        bitmap = page.render(scale=scale)
        return bitmap.to_pil().convert("RGB")
    finally:
        if bitmap is not None:
            bitmap.close()
        page.close()


class PDFTextExtractor:
    def __init__(
        self,
        pdf_path: Path,
        mode: str = "auto",
        tesseract_cmd: str = "",
        ocr_language: str = LANGUAGES,
        ocr_dpi: int = 250,
        ocr_psm: int = 3,
        exclude_tables: bool = True,
        exclude_images: bool = True,
        exclude_captions: bool = True,
        use_ollama: bool = False,
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "",
    ):
        self.pdf_path = Path(pdf_path)
        self.mode = mode
        self.tesseract_cmd = tesseract_cmd.strip()
        self.ocr_language = ocr_language.strip() or LANGUAGES
        self.ocr_dpi = ocr_dpi
        self.ocr_psm = ocr_psm
        self.exclude_tables = exclude_tables
        self.exclude_images = exclude_images
        self.exclude_captions = exclude_captions
        self.use_ollama = use_ollama
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.diagnostics: dict[int, dict] = {}

    @property
    def page_count(self) -> int:
        from pypdf import PdfReader

        return len(PdfReader(str(self.pdf_path)).pages)

    def extract(
        self,
        page_indices: list[int],
        progress: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[int, str]:
        from pypdf import PdfReader

        if self.mode not in {"auto", "text", "ocr"}:
            raise ValueError(f"未対応の抽出モードです: {self.mode}")
        reader = PdfReader(str(self.pdf_path))
        result: dict[int, str] = {}
        pdfium_document = None
        ocr_api = None
        plumber_document = None
        if self.mode != "ocr" and (self.exclude_tables or self.exclude_images):
            try:
                import pdfplumber

                plumber_document = pdfplumber.open(str(self.pdf_path))
            except ImportError as exc:
                raise RuntimeError("レイアウト除外には pdfplumber が必要です。") from exc
        ollama = (
            OllamaContentFilter(self.ollama_url, self.ollama_model)
            if self.use_ollama
            else None
        )
        try:
            for position, page_index in enumerate(page_indices, start=1):
                if should_stop and should_stop():
                    raise InterruptedError("PDF解析を停止しました。")
                if not 0 <= page_index < len(reader.pages):
                    raise ValueError(f"ページ番号が範囲外です: {page_index + 1}")
                diagnostic: dict = {
                    "method": "text",
                    "excluded_tables": 0,
                    "excluded_images": 0,
                    "ollama_removed": [],
                }
                text = ""
                candidate = ""
                if self.mode != "ocr":
                    if plumber_document is not None:
                        text, layout_info = self._extract_native_filtered(
                            plumber_document.pages[page_index]
                        )
                        diagnostic.update(layout_info)
                    else:
                        text = reader.pages[page_index].extract_text() or ""
                    candidate = text
                    if self.exclude_captions:
                        text = remove_caption_lines(text)
                    text = clean_extracted_text(text)
                needs_ocr = self.mode == "ocr" or (
                    self.mode == "auto" and len(text) < 10
                )
                page_image = None
                if needs_ocr or ollama is not None:
                    if pdfium_document is None:
                        try:
                            import pypdfium2 as pdfium
                            import pytesseract
                        except ImportError as exc:
                            raise RuntimeError(
                                "OCRには pypdfium2 と pytesseract が必要です。"
                            ) from exc
                        if self.tesseract_cmd:
                            pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd
                        pdfium_document = pdfium.PdfDocument(str(self.pdf_path))
                    scale = self.ocr_dpi / 72.0
                    page_image = _render_pdfium_page(
                        pdfium_document, page_index, scale
                    )
                method = "テキスト層"
                try:
                    if needs_ocr:
                        import pytesseract

                        ocr_image = page_image
                        if self.exclude_tables and page_image is not None:
                            regions = detect_table_regions(page_image)
                            diagnostic["excluded_tables"] = len(regions)
                            ocr_image = mask_regions(page_image, regions)
                        try:
                            if DATA.is_dir() and not self.tesseract_cmd:
                                from tesserocr import PyTessBaseAPI
                                if ocr_api is None:
                                    ocr_api = PyTessBaseAPI(path=str(DATA), lang=self.ocr_language, psm=self.ocr_psm)
                                    missing = set(self.ocr_language.split("+")) - set(ocr_api.GetLoadedLanguages())
                                    if missing:
                                        raise RuntimeError("OCR言語データが不足しています: " + ", ".join(sorted(missing)))
                                ocr_api.SetImage(ocr_image)
                                raw_text = ocr_api.GetUTF8Text()
                            else:
                                pytesseract.pytesseract.tesseract_cmd = self.tesseract_cmd or "tesseract"
                                raw_text = pytesseract.image_to_string(
                                    ocr_image, lang=self.ocr_language, config=f"--psm {self.ocr_psm}",
                                )
                        except pytesseract.TesseractNotFoundError as exc:
                            raise RuntimeError(
                                "Tesseract OCRが見つかりません。設定画面でtesseract.exeを指定してください。"
                            ) from exc
                        finally:
                            if ocr_image is not page_image:
                                ocr_image.close()
                        candidate = raw_text
                        if self.exclude_captions:
                            raw_text = remove_caption_lines(raw_text)
                        text = clean_extracted_text(raw_text)
                        method = "OCR"
                        diagnostic["method"] = "ocr"
                    # Inspect the unjoined lines for captions or repeated cell-like rows.
                    complex_layout = bool(re.search(
                        r"(?im)^\s*(?:図|表|写真|fig(?:ure)?\.?|table)\s*[\d０-９]", candidate
                    )) or sum(bool(re.search(r"\S(?:\s{2,}|\t)\S|\d+\s+\d+\s+\d+", line))
                              for line in candidate.splitlines()) >= 2
                    if ollama and needs_ocr and page_image and not complex_layout:
                        # Dense shaded patches are typical of photographs and diagrams.
                        from PIL import ImageStat
                        thumb = page_image.convert("L").resize((96, 96))
                        complex_layout = any(
                            30 < ImageStat.Stat(thumb.crop((x,y,x+12,y+12))).mean[0] < 170
                            for x in range(0,96,12) for y in range(0,96,12)
                        )
                    diagnostic["complex_layout"] = complex_layout
                    if ollama is not None and text and complex_layout:
                        text, removed = ollama.filter_body_text(text, page_image)
                        diagnostic["ollama_removed"] = removed
                        method += "+Ollama"
                        diagnostic["method"] += "+ollama"
                finally:
                    if page_image is not None:
                        page_image.close()
                self.diagnostics[page_index] = diagnostic
                result[page_index] = text
                if progress:
                    details = []
                    if diagnostic["excluded_tables"]:
                        details.append(f"表{diagnostic['excluded_tables']}件除外")
                    if diagnostic["excluded_images"]:
                        details.append(f"画像{diagnostic['excluded_images']}件除外")
                    suffix = ", " + ", ".join(details) if details else ""
                    progress(
                        position,
                        len(page_indices),
                        f"{page_index + 1}ページ ({method}{suffix})",
                    )
        finally:
            if ocr_api is not None:
                ocr_api.End()
            if plumber_document is not None:
                plumber_document.close()
            if pdfium_document is not None:
                pdfium_document.close()
        return result

    def _extract_native_filtered(self, page) -> tuple[str, dict]:
        excluded: list[tuple[float, float, float, float]] = []
        tables = []
        if self.exclude_tables:
            try:
                tables = page.find_tables()
                excluded.extend(tuple(table.bbox) for table in tables)
            except Exception:
                # Some malformed PDFs break only table detection; keep normal text usable.
                tables = []
        images = []
        if self.exclude_images:
            for image in page.images:
                box = (
                    float(image.get("x0", 0)),
                    float(image.get("top", 0)),
                    float(image.get("x1", 0)),
                    float(image.get("bottom", 0)),
                )
                area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
                # A full-page image is usually a scan background; text-layer OCR should
                # not be discarded merely because it overlaps that background.
                if area / max(1.0, page.width * page.height) < 0.85:
                    images.append(box)
                    excluded.append(box)

        def keep_object(obj: dict) -> bool:
            if obj.get("object_type") != "char" or not excluded:
                return True
            x = (float(obj.get("x0", 0)) + float(obj.get("x1", 0))) / 2
            y = (float(obj.get("top", 0)) + float(obj.get("bottom", 0))) / 2
            return not any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in excluded)

        filtered = page.filter(keep_object)
        return filtered.extract_text(x_tolerance=2, y_tolerance=3) or "", {
            "excluded_tables": len(tables),
            "excluded_images": len(images),
        }

