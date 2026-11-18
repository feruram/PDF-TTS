from __future__ import annotations

import json
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from autopdf.audio import concatenate_wav_files
from autopdf.autobook import create_autobook_package, inspect_autobook
from autopdf.layout_filter import (
    OllamaContentFilter,
    detect_table_regions,
    remove_caption_lines,
)
from autopdf.models import PageTurnAction, ScanProfile, TTSSettings
from autopdf.pdf_text import chunk_text, parse_page_range
from autopdf.pdf_text import PDFTextExtractor
from autopdf.sbv2 import StyleBertVITS2Client
from autopdf.scanner import ScanEngine, images_to_pdf, page_difference_percent


def make_wav(path: Path, frames: int, value: int = 0) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(value.to_bytes(2, "little", signed=True) * frames)


class CoreTests(unittest.TestCase):
    def test_a4_scan_keeps_capture_pixels_and_fills_page(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = ScanProfile(page_count=1, start_delay=0, fit_a4=True)
            with patch("autopdf.scanner.ImageGrab.grab", return_value=Image.new("RGB", (610, 845), "blue")):
                output = ScanEngine(root).run(profile, threading.Event(), lambda *_: None)

            page = PdfReader(output).pages[0]
            self.assertAlmostEqual(float(page.mediabox.height), 841.92, places=2)
            image, = page.images
            self.assertEqual(image.image.size, (610, 845))
            matrix, = [args for args, operator in page.get_contents().operations if operator == b"cm"]
            self.assertAlmostEqual(float(matrix[0]), float(page.mediabox.width), places=2)
            self.assertAlmostEqual(float(matrix[0]) / float(matrix[3]), 610 / 845, places=4)

    def test_profile_round_trip(self):
        profile = ScanProfile(actions=[PageTurnAction("click", {"x": 10, "y": 20})])
        restored = ScanProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.actions[0].kind, "click")
        self.assertEqual(restored.actions[0].params["x"], 10)

    def test_page_range(self):
        self.assertEqual(parse_page_range("1-3,5,8-", 10), [0, 1, 2, 4, 7, 8, 9])
        self.assertEqual(parse_page_range("", 3), [0, 1, 2])
        with self.assertRaises(ValueError):
            parse_page_range("5", 3)

    def test_japanese_chunking_respects_limit(self):
        source = "これは最初の文です。これは二番目の少し長い文です！最後です。"
        chunks = chunk_text(source, 12)
        self.assertTrue(chunks)
        self.assertTrue(all(len(item) <= 12 for item in chunks))
        self.assertEqual("".join(chunks), source)

    def test_page_difference(self):
        white = Image.new("RGB", (100, 100), "white")
        black = Image.new("RGB", (100, 100), "black")
        self.assertEqual(page_difference_percent(white, white), 0.0)
        self.assertGreater(page_difference_percent(white, black), 99.0)

    def test_caption_removal(self):
        text = "本文です。\n図1 構成図\n続きの本文です。\nTable 2: values"
        self.assertEqual(
            remove_caption_lines(text), "本文です。\n続きの本文です。"
        )

    def test_ruled_table_detection(self):
        image = Image.new("RGB", (800, 1000), "white")
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        for x in (100, 300, 500, 700):
            draw.line((x, 250, x, 650), fill="black", width=5)
        for y in (250, 350, 450, 550, 650):
            draw.line((100, y, 700, y), fill="black", width=5)
        regions = detect_table_regions(image)
        self.assertEqual(len(regions), 1)
        self.assertLessEqual(regions[0][0], 100)
        self.assertGreaterEqual(regions[0][2], 700)

    def test_native_layout_region_filter(self):
        class Table:
            bbox = (0, 0, 30, 30)

        class Filtered:
            def __init__(self, chars, predicate):
                self.chars = chars
                self.predicate = predicate

            def extract_text(self, **_kwargs):
                return "".join(
                    item["text"] for item in self.chars if self.predicate(item)
                )

        class Page:
            width = 100
            height = 100
            images = [{"x0": 65, "top": 65, "x1": 90, "bottom": 90}]
            chars = [
                {"object_type": "char", "text": "表", "x0": 5, "x1": 10, "top": 5, "bottom": 10},
                {"object_type": "char", "text": "本", "x0": 45, "x1": 50, "top": 45, "bottom": 50},
                {"object_type": "char", "text": "図", "x0": 70, "x1": 75, "top": 70, "bottom": 75},
            ]

            def find_tables(self):
                return [Table()]

            def filter(self, predicate):
                return Filtered(self.chars, predicate)

        extractor = PDFTextExtractor(Path("unused.pdf"))
        text, info = extractor._extract_native_filtered(Page())
        self.assertEqual(text, "本")
        self.assertEqual(info, {"excluded_tables": 1, "excluded_images": 1})

    def test_wav_concat_with_silence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, output = root / "a.wav", root / "b.wav", root / "out.wav"
            make_wav(first, 80, 1)
            make_wav(second, 80, 2)
            concatenate_wav_files([first, second], output, silence_ms=10)
            with wave.open(str(output), "rb") as stream:
                self.assertEqual(stream.getnframes(), 80 + 80 + 80)

    def test_images_to_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for index, color in enumerate(("red", "blue")):
                path = root / f"{index}.jpg"
                Image.new("RGB", (64, 48), color).save(path)
                paths.append(path)
            output = root / "out.pdf"
            images_to_pdf(paths, output)
            self.assertTrue(output.read_bytes().startswith(b"%PDF"))

    def test_autobook_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_image = root / "page.jpg"
            Image.new("RGB", (64, 96), "white").save(source_image)
            source_pdf = root / "book.pdf"
            images_to_pdf([source_image], source_pdf)
            job = root / "book_tts"
            chunks = job / "chunks"
            chunks.mkdir(parents=True)
            first = chunks / "p0001_001.wav"
            second = chunks / "p0001_002.wav"
            make_wav(first, 800, 1)
            make_wav(second, 800, 2)
            combined = job / "book_audiobook.wav"
            concatenate_wav_files([first, second], combined, silence_ms=100)
            manifest = {
                "source_pdf": str(source_pdf),
                "output_wav": str(combined),
                "settings": {"silence_ms": 100},
                "items": [
                    {"page": 1, "chunk": 1, "text": "前半", "wav": "chunks/p0001_001.wav"},
                    {"page": 1, "chunk": 2, "text": "後半", "wav": "chunks/p0001_002.wav"},
                ],
            }
            (job / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            (job / "transcript.txt").write_text("前半\n後半", encoding="utf-8")
            package = create_autobook_package(job)
            inspected = inspect_autobook(package)
            self.assertEqual(inspected["format"], "autobook")
            self.assertEqual(len(inspected["timeline"]), 2)
            self.assertIn("audio/book.mp3", inspected["package_files"])


class _APIHandler(BaseHTTPRequestHandler):
    wav_bytes = b""

    def do_GET(self):
        if self.path == "/models/info":
            body = json.dumps(
                {
                    "0": {
                        "model_path": "model_assets/test/model.safetensors",
                        "config_path": "model_assets/test/config.json",
                        "spk2id": {"speaker": 0},
                        "style2id": {"Neutral": 0},
                    }
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/tags":
            body = json.dumps({"models": [{"name": "vision-test:latest"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        if self.path.startswith("/voice?"):
            body = self.wav_bytes
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/chat":
            body = json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "body_text": "これは本文です。",
                                "removed_categories": ["table", "image"],
                            },
                            ensure_ascii=False,
                        ),
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def log_message(self, _format, *_args):
        pass


class APIClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            wav_path = Path(temp) / "sample.wav"
            make_wav(wav_path, 20)
            _APIHandler.wav_bytes = wav_path.read_bytes()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _APIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_models_and_synthesis(self):
        client = StyleBertVITS2Client(self.url)
        models = client.get_models()
        self.assertEqual(models[0].model_name, "test")
        settings = TTSSettings(api_url=self.url, model_name="test", speaker_name="speaker")
        audio = client.synthesize("テスト", settings)
        self.assertEqual(audio[:4], b"RIFF")

    def test_ollama_filter(self):
        client = OllamaContentFilter(self.url, "vision-test:latest")
        self.assertEqual(client.list_models(), ["vision-test:latest"])
        body, removed = client.filter_body_text(
            "これは本文です。\n表1 数値", Image.new("RGB", (32, 32), "white")
        )
        self.assertEqual(body, "これは本文です。")
        self.assertEqual(removed, ["table", "image"])


if __name__ == "__main__":
    unittest.main()

