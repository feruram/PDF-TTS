"""Capture the displayed Chrome viewport into a resumable local book."""
import hashlib
import io
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageOps
from .scanner import ScanCancelled

from autopdf.scanner import images_to_pdf, page_difference_percent


def thumbnail(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("L").resize((160, 160))


def save_page(image, destination, size):
    if image.size == size:
        image.save(destination, format="PNG")
    else:
        fitted = ImageOps.pad(image.convert("RGB"), size, method=Image.Resampling.LANCZOS, color="white")
        fitted.save(destination, format="PNG")


def settled_page(page, previous, stop):
    deadline = time.monotonic() + 25
    last = None
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        if stop.is_set():
            raise ScanCancelled()
        data = page.screenshot(type="png", animations="disabled", timeout=15000)
        sample = thumbnail(data)
        if last is None or page_difference_percent(last, sample) > 0.05:
            stable_since = time.monotonic()
        low, high = sample.getextrema()
        changed = high - low > 12 and (previous is None or page_difference_percent(previous, sample) > 0.15)
        if changed and time.monotonic() - stable_since >= 1.2:
            return data, sample
        last = sample
        time.sleep(0.25)
    raise ValueError("ページの変化・安定を確認できません。保存済み枚数を確認して再開してください。")


def assemble(paths, destination, area=None, output_size=None):
    import pikepdf
    with Image.open(paths[0]) as first:
        reference = first.size
    # Import small batches so img2pdf does not accumulate a whole book of PNGs.
    with TemporaryDirectory(dir=destination.parent) as temp, pikepdf.Pdf.new() as book:
        chunk = Path(temp) / "batch.pdf"
        for index in range(0, len(paths), 12):
            batch = paths[index:index+12]
            if area or output_size:
                cropped = []
                for slot, path in enumerate(batch):
                    target = Path(temp) / f"crop-{slot}.png"
                    with Image.open(path) as image:
                        if not area and image.size == output_size:
                            cropped.append(path)
                            continue
                        box = area or (0, 0, *reference)
                        left, top, right, bottom = (
                            round(value * image.size[i % 2] / reference[i % 2])
                            for i, value in enumerate(box)
                        )
                        if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                            raise ValueError("指定した範囲が撮影画像からはみ出しています。")
                        region = image.crop((left, top, right, bottom))
                        save_page(region, target, output_size or region.size)
                    cropped.append(target)
                batch = cropped
            images_to_pdf(batch, chunk)
            with pikepdf.Pdf.open(chunk) as part:
                book.pages.extend(part.pages)
        finished = Path(temp) / "finished.pdf"
        book.save(finished)
        finished.replace(destination)


def run(root, url, count, key, position, confirm, report, stop, delay=1.0, select_area=None, output_size=(1400, 2000)):
    from playwright.sync_api import sync_playwright
    job = root / ("chrome-" + hashlib.sha256(url.encode()).hexdigest()[:16])
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-*.png"), key=lambda path: int(path.stem[5:]))
    if any(path.name != f"page-{n:04}.png" for n, path in enumerate(paths, 1)) or len(paths) > count:
        raise ValueError("保存画像の連番または総ページ数を確認してください。")
    area = select_area(paths[0]) if paths and select_area else None
    if len(paths) < count:
        with sync_playwright() as browser:
            context = browser.chromium.launch_persistent_context(
                str(Path(os.environ["LOCALAPPDATA"]) / "AutoPDF" / "Chrome"),
                channel="chrome", headless=False, viewport={"width":1400,"height":2000},
                device_scale_factor=1, args=["--window-size=1000,900"],
            )
            page = context.pages[0]
            page.goto(url, wait_until="domcontentloaded")
            confirm(f"Chromeで開始位置（今回の {len(paths)+1} 枚目）を表示してから、撮影を開始してください。", None)
            page = context.pages[-1]
            previous = thumbnail(paths[-1].read_bytes()) if paths else None
            for number in range(len(paths)+1, count+1):
                data, previous = settled_page(page, previous, stop)
                path = job / f"page-{number:04}.png"
                tmp = path.with_suffix(".tmp")
                with Image.open(io.BytesIO(data)) as image:
                    save_page(image, tmp, output_size)
                tmp.replace(path)
                paths.append(path)
                if number == 1 and select_area:
                    area = select_area(path)
                report(f"撮影済み {number} / {count} 枚")
                if number < count:
                    if number == 1 and not select_area:
                        confirm("最初の撮影画像です。範囲と画質を確認してください。", path)
                    if position:
                        page.mouse.click(*position)
                    else:
                        page.keyboard.press("Arrow" + key if key in ("Left","Right") else key)
                    if stop.wait(delay):
                        raise ScanCancelled()
    destination = job / "book.pdf"
    if stop.is_set():
        raise ScanCancelled()
    report("PDFを作成中…")
    assemble(paths, destination, area, output_size)
    return destination
