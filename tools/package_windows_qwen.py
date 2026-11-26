"""Build the release ZIP from AutoPDF.exe, the official Ollama ZIP and a verified model store."""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def package(exe, runtime, license_file, models, output):
    if digest(runtime) != "ca97292ac1e9acc77fcf473bb2741fa6acd12e3798f261b419680068b7cc4c16":
        raise ValueError("Expected the official Ollama v0.40.18 Windows x64 ZIP")
    manifest = models / "manifests/registry.ollama.ai/library/qwen3.5/2b"
    if digest(manifest) != "1e2d21a4f03ab9e4a9c1976a4b7e88f21de61d65f2c5485d8fde8942be73f1e7":
        raise ValueError("Expected the pinned official qwen3.5:2b-q4_K_M manifest")
    data = json.loads(manifest.read_bytes())
    partial = output.with_suffix(".partial")
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
            bundle.write(exe, "AutoPDF.exe", compress_type=zipfile.ZIP_STORED)
            bundle.write(license_file, "qwen/OLLAMA_LICENSE")
            with zipfile.ZipFile(runtime) as upstream:
                for item in upstream.infolist():
                    name = item.filename
                    if not item.is_dir() and (name == "ollama.exe" or
                            (name.startswith("lib/ollama/") and name.count("/") == 2)):
                        bundle.writestr("qwen/" + name, upstream.read(item))
            bundle.write(manifest, "qwen/models/" + manifest.relative_to(models).as_posix())
            for layer in [data["config"], *data["layers"]]:
                blob = models / "blobs" / layer["digest"].replace(":", "-")
                if digest(blob) != layer["digest"].split(":")[1]:
                    raise ValueError(f"Invalid model blob: {blob.name}")
                bundle.write(blob, "qwen/models/blobs/" + blob.name)
                if layer["mediaType"].endswith(".license"):
                    bundle.write(blob, "qwen/QWEN_LICENSE")
        if partial.stat().st_size >= 2 ** 31:
            raise ValueError("ZIP exceeds GitHub's individual release asset limit")
        with zipfile.ZipFile(partial) as bundle:
            if bundle.testzip():
                raise ValueError("ZIP verification failed")
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("exe", "runtime", "license_file", "models", "output"):
        parser.add_argument(name, type=Path)
    package(**vars(parser.parse_args()))
