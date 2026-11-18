"""Capture the displayed Chrome viewport into a resumable local book."""
import hashlib
import io
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
from playwright.sync_api import sync_playwright

from autopdf.scanner import images_to_pdf, page_difference_percent


def thumbnail(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("L").resize((160, 160))


def settled_page(page, previous):
    deadline = time.monotonic() + 25
    last = None
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
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


def assemble(paths, destination):
    import pikepdf
    # Import small batches so img2pdf does not accumulate a whole book of PNGs.
    with TemporaryDirectory(dir=destination.parent) as temp, pikepdf.Pdf.new() as book:
        chunk = Path(temp) / "batch.pdf"
        for index in range(0, len(paths), 12):
            images_to_pdf(paths[index:index+12], chunk, page_size=(595.28, 841.89))
            with pikepdf.Pdf.open(chunk) as part:
                book.pages.extend(part.pages)
        finished = Path(temp) / "finished.pdf"
        book.save(finished)
        finished.replace(destination)


def run():
    url = input("教本ビューアのURL: ").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("http(s)のURLを入力してください。")
    count = int(input("総ページ数 [810]: ").strip() or 810)
    if not 1 <= count <= 9999:
        raise ValueError("総ページ数は1～9999です。")
    key = input("次ページのキー [Right] (Left / PageDown / click): ").strip() or "Right"
    position = None
    if key == "click":
        position = tuple(map(int, input("次へボタンの仮想画面座標 x,y: ").split(",")))
        if len(position) != 2:
            raise ValueError("x,y の2値を指定してください。")
    elif key not in ("Right", "Left", "PageDown"):
        raise ValueError("表示されたキーから選んでください。")
    root = Path(__file__).resolve().parent / "output"
    job = root / ("chrome-" + hashlib.sha256(url.encode()).hexdigest()[:16])
    job.mkdir(parents=True, exist_ok=True)
    paths = sorted(job.glob("page-????.png"))
    if any(path.name != f"page-{n:04}.png" for n, path in enumerate(paths, 1)) or len(paths) > count:
        raise ValueError("保存画像の連番または総ページ数を確認してください。")
    if len(paths) < count:
        with sync_playwright() as browser:
            context = browser.chromium.launch_persistent_context(
                str(Path(os.environ["LOCALAPPDATA"]) / "AutoPDF" / "Chrome"),
                channel="chrome", headless=False, viewport={"width":1400,"height":2000},
                device_scale_factor=1, args=["--window-size=1000,900"],
            )
            page = context.pages[0]
            page.goto(url, wait_until="domcontentloaded")
            input(f"Chromeでログインし、教本の {len(paths)+1} ページ目を表示してください。"
                  "必要なら本文をクリックしてから、この画面でEnter: ")
            page = context.pages[-1]
            print("撮影中。Ctrl+Cで中断できます。保存先:", job)
            previous = thumbnail(paths[-1].read_bytes()) if paths else None
            for number in range(len(paths)+1, count+1):
                data, previous = settled_page(page, previous)
                path = job / f"page-{number:04}.png"
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)
                paths.append(path)
                print(f"{number}/{count}", flush=True)
                if number < count:
                    if number == 1:
                        print("最初の撮影画像:", path)
                        input("このPNGでページ全体・画質を確認し、残りを自動撮影するにはEnter: ")
                    if position:
                        page.mouse.click(*position)
                    else:
                        page.keyboard.press("Arrow" + key if key in ("Left","Right") else key)
                    time.sleep(0.75)
    destination = job / "book.pdf"
    assemble(paths, destination)
    print("PDF完成:", destination)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("中断しました。保存済み画像から再開できます。")
