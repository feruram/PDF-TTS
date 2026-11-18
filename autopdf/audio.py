from __future__ import annotations

import contextlib
import wave
from pathlib import Path


def concatenate_wav_files(
    paths: list[Path], output_path: Path, silence_ms: int = 250
) -> None:
    if not paths:
        raise ValueError("連結するWAVファイルがありません。")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(wave.open(str(paths[0]), "rb")) as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    for path in paths[1:]:
        with contextlib.closing(wave.open(str(path), "rb")) as current:
            comparable = (
                current.getnchannels(),
                current.getsampwidth(),
                current.getframerate(),
                current.getcomptype(),
            )
            expected = (
                params.nchannels,
                params.sampwidth,
                params.framerate,
                params.comptype,
            )
            if comparable != expected:
                raise ValueError(f"WAV形式が一致しません: {path.name}")
            frames.append(current.readframes(current.getnframes()))
    silence_frames = int(params.framerate * max(0, silence_ms) / 1000)
    silence = b"\x00" * silence_frames * params.nchannels * params.sampwidth
    with contextlib.closing(wave.open(str(output_path), "wb")) as target:
        target.setnchannels(params.nchannels)
        target.setsampwidth(params.sampwidth)
        target.setframerate(params.framerate)
        target.setcomptype(params.comptype, params.compname)
        for index, data in enumerate(frames):
            if index:
                target.writeframesraw(silence)
            target.writeframesraw(data)
        target.writeframes(b"")

