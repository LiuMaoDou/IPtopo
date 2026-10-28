"""Machine-local AI connection preferences. Credentials are environment references."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    backend: Literal["codex", "opencode", "openai"] = "codex"
    model: str = Field(default="", max_length=200)
    codex_path: str = Field(default="codex", min_length=1, max_length=4096)
    opencode_url: str = "http://127.0.0.1:4096"
    opencode_api: Literal["v1", "v2"] = "v1"
    api_url: str = "https://api.openai.com/v1"
    api_protocol: Literal["auto", "responses", "chat_completions"] = "auto"
    api_key_env: str = "OPENAI_API_KEY"
    opencode_password_env: str = "OPENCODE_SERVER_PASSWORD"
    timeout_seconds: int = Field(default=120, ge=15, le=600)

    @field_validator("api_url", "opencode_url")
    @classmethod
    def service_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(ord(c) < 32 for c in value)):
            raise ValueError("服务地址须为 HTTP(S) 地址，不能包含凭据、查询参数或片段")
        try:
            parsed.port
        except ValueError:
            raise ValueError("服务端口无效") from None
        return value.rstrip("/")

    @field_validator("api_key_env", "opencode_password_env")
    @classmethod
    def environment_reference(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
            raise ValueError("请填写存放凭据的环境变量名称，不能直接填写密钥")
        return value

    @field_validator("codex_path", "model")
    @classmethod
    def plain_text(cls, value: str, info) -> str:
        if any(ord(c) < 32 for c in value):
            raise ValueError("字段不能包含控制字符")
        value = value.strip()
        if info.field_name == "codex_path" and not value:
            raise ValueError("Codex 程序路径不能为空")
        return value


class ConfigStore:
    def __init__(self, data_dir: str | Path):
        self.path = Path(data_dir) / "ai" / "settings.json"
        self._lock = RLock()

    def load(self) -> dict:
        with self._lock:
            if not self.path.exists():
                return Settings().model_dump()
            try:
                if self.path.stat().st_size > 65536:
                    raise ValueError()
                return Settings.model_validate(json.loads(self.path.read_text(encoding="utf-8"))).model_dump()
            except (ValueError, OSError):
                raise ValueError("AI 设置文件无效，请重新保存连接设置") from None

    def save(self, values: dict) -> dict:
        with self._lock:
            try:
                previous = self.load()
            except ValueError:
                previous = Settings().model_dump()
            settings = Settings.model_validate({**previous, **values}).model_dump()
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                    json.dump(settings, target, ensure_ascii=False, indent=2)
                    target.write("\n")
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return settings

    def provider_settings(self) -> dict:
        """Internal runtime paths are never accepted from or sent to clients."""
        return {**self.load(), "_codex_home": str(self.path.parent.resolve()/"codex-home")}
