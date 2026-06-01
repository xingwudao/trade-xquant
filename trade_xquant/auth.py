from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOKEN_FILE_NAME = "xquant-token.json"


def token_path_for_config(config_path: str | Path) -> Path:
    return Path(config_path).resolve().parent / TOKEN_FILE_NAME


class TokenStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, access_token: str, token_type: str = "bearer") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": access_token,
            "token_type": token_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def load_access_token(self) -> str | None:
        payload = self.load()
        if not payload:
            return None
        token = payload.get("access_token")
        return str(token) if token else None
