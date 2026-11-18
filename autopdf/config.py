from __future__ import annotations

import json
from pathlib import Path

from .models import ScanProfile, TTSSettings


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> tuple[dict[str, ScanProfile], TTSSettings, dict]:
        if not self.path.exists():
            profile = ScanProfile()
            return {profile.name: profile}, TTSSettings(), {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            profiles = {
                name: ScanProfile.from_dict(value)
                for name, value in raw.get("profiles", {}).items()
            }
            if not profiles:
                default = ScanProfile()
                profiles[default.name] = default
            tts = TTSSettings(**raw.get("tts", {}))
            ui = dict(raw.get("ui", {}))
            return profiles, tts, ui
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"設定ファイルを読み込めません: {exc}") from exc

    def save(
        self,
        profiles: dict[str, ScanProfile],
        tts: TTSSettings,
        ui: dict | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "profiles": {name: profile.to_dict() for name, profile in profiles.items()},
            "tts": tts.__dict__,
            "ui": ui or {},
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

