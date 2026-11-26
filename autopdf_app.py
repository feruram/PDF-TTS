import sys
from pathlib import Path

from autopdf.gui import launch


if __name__ == "__main__":
    base = sys.executable if getattr(sys, "frozen", False) else __file__
    launch(Path(base).resolve().parent)

