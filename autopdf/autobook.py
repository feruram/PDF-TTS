from __future__ import annotations

import hashlib
import json
import tempfile
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path


AUTOBOOK_VERSION = 1


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as stream:
        return round(stream.getnframes() / stream.getframerate() * 1000)


def _convert_wav_to_mp3(input_path: Path, output_path: Path) -> None:
    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError("Android用音声圧縮には soundfile が必要です。") from exc
    with sf.SoundFile(str(input_path), "r") as source:
        with sf.SoundFile(
            str(output_path),
            "w",
            samplerate=source.samplerate,
            channels=source.channels,
            format="MP3",
            subtype="MPEG_LAYER_III",
        ) as target:
            while True:
                block = source.read(65536, dtype="float32", always_2d=True)
                if len(block) == 0:
                    break
                target.write(block)


def _create_cover(source_pdf: Path, output_path: Path) -> bool:
    document = None
    page = None
    bitmap = None
    image = None
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(source_pdf))
        if len(document) == 0:
            return False
        page = document[0]
        bitmap = page.render(scale=1.5)
        image = bitmap.to_pil().convert("RGB")
        image.thumbnail((720, 1080))
        image.save(output_path, "JPEG", quality=86, optimize=True)
        return True
    except Exception:
        return False
    finally:
        if image is not None:
            image.close()
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()
        if document is not None:
            document.close()


def create_autobook_package(
    job_dir: Path,
    output_path: Path | None = None,
    title: str | None = None,
) -> Path:
    """Create a portable, offline Android playback package.

    The resulting ``.autobook`` is a ZIP file containing one Ogg/Vorbis audio
    stream, a cover thumbnail, transcript, and per-chunk timing metadata.
    """

    job_dir = Path(job_dir)
    source_manifest_path = job_dir / "manifest.json"
    if not source_manifest_path.is_file():
        raise ValueError(f"manifest.jsonが見つかりません: {job_dir}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_pdf = Path(source_manifest["source_pdf"])
    source_wav = Path(source_manifest["output_wav"])
    if not source_wav.is_file():
        raise ValueError(f"読み上げWAVが見つかりません: {source_wav}")
    stem = source_pdf.stem
    output_path = Path(output_path or job_dir / f"{stem}.autobook")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    silence_ms = int(source_manifest.get("settings", {}).get("silence_ms", 0))

    timeline = []
    cursor_ms = 0
    for item in source_manifest.get("items", []):
        chunk_path = job_dir / item["wav"]
        duration_ms = _wav_duration_ms(chunk_path)
        timeline.append(
            {
                "page": int(item["page"]),
                "chunk": int(item["chunk"]),
                "start_ms": cursor_ms,
                "duration_ms": duration_ms,
                "text": str(item["text"]),
            }
        )
        cursor_ms += duration_ms + silence_ms

    with tempfile.TemporaryDirectory(prefix="autobook-") as temp:
        temp_dir = Path(temp)
        audio_path = temp_dir / "book.mp3"
        _convert_wav_to_mp3(source_wav, audio_path)
        cover_path = temp_dir / "cover.jpg"
        has_cover = _create_cover(source_pdf, cover_path)
        digest = hashlib.sha256()
        with source_pdf.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        package_manifest = {
            "format": "autobook",
            "version": AUTOBOOK_VERSION,
            "book_id": digest.hexdigest(),
            "title": title or stem,
            "language": "ja-JP",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "audio": {
                "file": "audio/book.mp3",
                "mime_type": "audio/mpeg",
                "duration_ms": max(0, cursor_ms - silence_ms),
            },
            "cover": "cover.jpg" if has_cover else None,
            "timeline": timeline,
            "extraction_diagnostics": source_manifest.get(
                "extraction_diagnostics", {}
            ),
        }
        package_json = temp_dir / "manifest.json"
        package_json.write_text(
            json.dumps(package_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        transcript = job_dir / "transcript.txt"
        temporary_package = output_path.with_suffix(output_path.suffix + ".tmp")
        with zipfile.ZipFile(temporary_package, "w") as archive:
            archive.write(package_json, "manifest.json", zipfile.ZIP_DEFLATED)
            archive.write(audio_path, "audio/book.mp3", zipfile.ZIP_STORED)
            if has_cover:
                archive.write(cover_path, "cover.jpg", zipfile.ZIP_STORED)
            if transcript.is_file():
                archive.write(transcript, "transcript.txt", zipfile.ZIP_DEFLATED)
        temporary_package.replace(output_path)
    return output_path


def inspect_autobook(path: Path) -> dict:
    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names or not names.intersection({"audio/book.ogg", "audio/book.mp3"}):
            raise ValueError("有効なAutoBookパッケージではありません。")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != "autobook" or manifest.get("version") != 1:
            raise ValueError("未対応のAutoBook形式です。")
        manifest["package_files"] = sorted(names)
        return manifest

