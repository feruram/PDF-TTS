"""Install the three official fast OCR models in this virtual environment."""
import hashlib
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import urlopen
from shutil import copyfileobj

DATA = Path(sys.prefix) / "share" / "autopdf-tessdata"
LANGUAGES = "jpn+eng+fra"
HASHES = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "fra": "ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca",
    "jpn": "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d",
}


def install():
    from tesserocr import PyTessBaseAPI
    DATA.mkdir(parents=True, exist_ok=True)
    for language in LANGUAGES.split("+"):
        target = DATA / (language + ".traineddata")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == HASHES[language]:
            continue
        url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/" + target.name
        with NamedTemporaryFile(dir=DATA, delete=False) as stream:
            temp = Path(stream.name)
        try:
            with urlopen(url, timeout=90) as response, temp.open("wb") as stream:
                copyfileobj(response, stream)
            if hashlib.sha256(temp.read_bytes()).hexdigest() != HASHES[language]:
                raise RuntimeError(f"Invalid language model checksum: {language}")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    with PyTessBaseAPI(path=str(DATA), lang=LANGUAGES) as api:
        loaded = set(api.GetLoadedLanguages())
        if set(LANGUAGES.split("+")) - loaded:
            raise RuntimeError(f"OCR languages missing: {loaded}")
        print("OCR ready:", LANGUAGES)


if __name__ == "__main__":
    install()
