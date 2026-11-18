from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from .audio import concatenate_wav_files
from .autobook import create_autobook_package
from .models import TTSSettings
from .pdf_text import PDFTextExtractor, chunk_text, parse_page_range
from .sbv2 import StyleBertVITS2Client


class TTSCancelled(Exception):
    pass


class PDFTTSJob:
    def __init__(
        self,
        pdf_path: Path,
        output_dir: Path,
        page_range: str,
        settings: TTSSettings,
        extraction_mode: str = "auto",
        tesseract_cmd: str = "",
        ocr_language: str = "jpn",
        exclude_tables: bool = True,
        exclude_images: bool = True,
        exclude_captions: bool = True,
        use_ollama: bool = False,
        ollama_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "",
    ):
        self.pdf_path = Path(pdf_path)
        self.output_dir = Path(output_dir)
        self.page_range = page_range
        self.settings = settings
        self.extractor = PDFTextExtractor(
            self.pdf_path,
            mode=extraction_mode,
            tesseract_cmd=tesseract_cmd,
            ocr_language=ocr_language,
            exclude_tables=exclude_tables,
            exclude_images=exclude_images,
            exclude_captions=exclude_captions,
            use_ollama=use_ollama,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
        )

    def extract_texts(
        self, progress=None, stop_event: threading.Event | None = None
    ) -> dict[int, str]:
        pages = parse_page_range(self.page_range, self.extractor.page_count)
        try:
            return self.extractor.extract(
                pages,
                progress=progress,
                should_stop=stop_event.is_set if stop_event else None,
            )
        except InterruptedError as exc:
            raise TTSCancelled from exc

    def run(
        self,
        stop_event: threading.Event,
        progress: Callable[[str, int, int], None],
        pre_extracted: dict[int, str] | None = None,
    ) -> Path:
        self.settings.validate()
        texts = pre_extracted or self.extract_texts(
            lambda done, total, msg: progress("文字抽出: " + msg, done, total),
            stop_event=stop_event,
        )
        work_items: list[tuple[int, int, str]] = []
        for page_index, text in texts.items():
            for chunk_index, chunk in enumerate(
                chunk_text(text, self.settings.max_chars), start=1
            ):
                work_items.append((page_index, chunk_index, chunk))
        if not work_items:
            raise ValueError("読み上げ可能な文字を抽出できませんでした。OCR設定を確認してください。")

        stem = self.pdf_path.stem
        job_dir = self.output_dir / f"{stem}_tts"
        chunk_dir = job_dir / "chunks"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        client = StyleBertVITS2Client(self.settings.api_url)
        wav_paths: list[Path] = []
        manifest_items: list[dict] = []
        for position, (page_index, chunk_index, text) in enumerate(work_items, start=1):
            if stop_event.is_set():
                raise TTSCancelled
            path = chunk_dir / f"p{page_index + 1:04d}_{chunk_index:03d}.wav"
            progress(
                f"音声合成: {page_index + 1}ページ / {text[:28]}",
                position - 1,
                len(work_items),
            )
            audio = client.synthesize(text, self.settings)
            path.write_bytes(audio)
            wav_paths.append(path)
            manifest_items.append(
                {
                    "page": page_index + 1,
                    "chunk": chunk_index,
                    "text": text,
                    "wav": str(path.relative_to(job_dir)),
                }
            )
            progress("音声チャンクを保存しました。", position, len(work_items))

        output_path = job_dir / f"{stem}_audiobook.wav"
        progress("WAVを連結しています。", len(work_items), len(work_items))
        concatenate_wav_files(wav_paths, output_path, self.settings.silence_ms)
        manifest = {
            "source_pdf": str(self.pdf_path.resolve()),
            "output_wav": str(output_path.resolve()),
            "settings": self.settings.__dict__,
            "extraction_diagnostics": {
                str(page + 1): info for page, info in self.extractor.diagnostics.items()
            },
            "items": manifest_items,
        }
        (job_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (job_dir / "transcript.txt").write_text(
            "\n".join(item["text"] for item in manifest_items), encoding="utf-8"
        )
        package_path = job_dir / f"{stem}.autobook"
        progress("Android用AutoBookを作成しています。", len(work_items), len(work_items))
        create_autobook_package(job_dir, package_path)
        progress("音声ファイルを作成しました。", len(work_items), len(work_items))
        return output_path

