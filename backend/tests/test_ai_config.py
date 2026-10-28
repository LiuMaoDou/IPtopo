import json
import os

import pytest
from pydantic import ValidationError

from backend.app.ai.config import ConfigStore, Settings


def test_machine_settings_are_atomic_and_contain_references_not_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_AI_KEY", "private-api-secret")
    store = ConfigStore(tmp_path)
    assert store.load()["backend"] == "codex"
    saved = store.save({"backend": "openai", "api_key_env": "TEST_AI_KEY", "model": "example-model"})
    assert saved["api_key_env"] == "TEST_AI_KEY"
    assert "private-api-secret" not in store.path.read_text(encoding="utf-8")
    assert ConfigStore(tmp_path).load() == saved
    assert list(store.path.parent.glob(".settings-*")) == []
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o077 == 0
    before = store.path.read_bytes()
    with pytest.raises(ValidationError):
        store.save({"api_key": "private-api-secret"})
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("values", [
    {"api_url": "https://user:password@example.com/v1"},
    {"api_url": "https://example.com/v1?key=secret"},
    {"opencode_url": "file:///etc/passwd"},
    {"opencode_url": "http://127.0.0.1:bad"},
    {"api_key_env": "sk-secret-key"},
    {"opencode_password_env": "x\nsecret"},
    {"codex_path": "\n/bin/codex"},
    {"codex_path": "   "},
    {"timeout_seconds": True},
    {"timeout_seconds": 1000},
    {"backend": "unknown"},
    {"api_protocol": "guess"},
])
def test_invalid_settings_are_rejected(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_invalid_or_oversize_stored_settings_do_not_leak_raw_values(tmp_path):
    store = ConfigStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text(json.dumps({"api_key": "PRIVATE-SECRET"}), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        store.load()
    assert "PRIVATE-SECRET" not in str(error.value)
    store.path.write_text("x"*65537, encoding="utf-8")
    with pytest.raises(ValueError):
        store.load()


def test_url_normalization_and_credentials_are_not_in_project_settings(tmp_path):
    saved = ConfigStore(tmp_path).save({"opencode_url": "http://[::1]:4096/", "api_url": "https://example.com/v1/"})
    assert saved["opencode_url"] == "http://[::1]:4096"
    assert saved["api_url"] == "https://example.com/v1"
    assert not (tmp_path/"settings.json").exists()


def test_corrupt_settings_can_be_replaced_without_persisting_internal_paths(tmp_path):
    store=ConfigStore(tmp_path)
    store.path.parent.mkdir()
    store.path.write_text('{invalid', encoding="utf-8")
    with pytest.raises(ValueError):store.load()
    saved=store.save({"backend":"openai","model":"restored"})
    assert saved["backend"]=="openai" and saved["model"]=="restored"
    internal=store.provider_settings()
    assert internal["_codex_home"]==str((tmp_path/"ai"/"codex-home").resolve())
    assert "_codex_home" not in store.load()
    assert "_codex_home" not in store.path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):Settings.model_validate(internal)
