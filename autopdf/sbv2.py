from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TTSSettings


@dataclass
class ModelInfo:
    model_id: int
    model_name: str
    speakers: dict[str, int]
    styles: dict[str, int]
    raw: dict[str, Any]


class StyleBertVITS2Client:
    def __init__(self, base_url: str, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, data: bytes | None = None) -> bytes:
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST" if data is not None else "GET",
            headers={"User-Agent": "AutoPDF/2.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Style-Bert-VITS2 APIエラー ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Style-Bert-VITS2 APIに接続できません: {self.base_url} ({exc.reason})"
            ) from exc

    def get_models(self) -> list[ModelInfo]:
        payload = json.loads(self._request("/models/info").decode("utf-8"))
        models: list[ModelInfo] = []
        for key, raw in sorted(payload.items(), key=lambda item: int(item[0])):
            model_path = Path(str(raw.get("model_path", "")))
            config_path = Path(str(raw.get("config_path", "")))
            parent = model_path.parent.name or config_path.parent.name
            name = parent or f"model-{key}"
            speakers = {str(k): int(v) for k, v in raw.get("spk2id", {}).items()}
            styles = {str(k): int(v) for k, v in raw.get("style2id", {}).items()}
            models.append(ModelInfo(int(key), name, speakers, styles, raw))
        return models

    def synthesize(self, text: str, settings: TTSSettings) -> bytes:
        settings.validate()
        params: dict[str, str | int | float | bool] = {
            "text": text,
            "model_id": settings.model_id,
            "speaker_id": settings.speaker_id,
            "sdp_ratio": settings.sdp_ratio,
            "noise": settings.noise,
            "noisew": settings.noisew,
            "length": settings.length,
            "language": settings.language,
            "auto_split": False,
            "style": settings.style,
            "style_weight": settings.style_weight,
        }
        if settings.model_name:
            params["model_name"] = settings.model_name
        if settings.speaker_name:
            params["speaker_name"] = settings.speaker_name
        query = urllib.parse.urlencode(params)
        audio = self._request("/voice?" + query, data=b"")
        if len(audio) < 12 or audio[:4] not in {b"RIFF", b"RF64"}:
            raise RuntimeError("API応答がWAV音声ではありません。")
        return audio

