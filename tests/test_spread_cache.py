import tempfile
import unittest
from pathlib import Path

from PIL import Image

from autopdf.chrome_capture import assemble
from autopdf.spread import remove_pdf_cache


class SpreadCacheTests(unittest.TestCase):
    def test_changed_last_page_preserves_entire_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pages = [root / "page-0001.png", root / "page-0002.png"]
            for path in pages:
                Image.new("RGB", (40, 60), "red").save(path)
            pdf = root / "book.pdf"
            assemble(pages, pdf)
            Image.new("RGB", (40, 60), "blue").save(pages[-1])
            with self.assertRaises(ValueError):
                remove_pdf_cache(pdf, pages)
            self.assertTrue(all(path.exists() for path in pages))
