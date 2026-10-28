"""Windows native-agent behavior, including platforms without symlink rights.

The resolver's explicit platform argument permits these checks on every CI
runner; no cmd.exe launcher, installed agent, credentials or model is used.
"""
import asyncio
from contextlib import contextmanager
import os

import pytest

from backend.app.ai import providers
from backend.app.ai.providers import ProviderError


@pytest.mark.parametrize("local", [False, True], ids=["global-npm", "local-npm"])
@pytest.mark.parametrize("machine,package,triple", [
    ("AMD64", "codex-win32-x64", "x86_64-pc-windows-msvc"),
    ("ARM64", "codex-win32-arm64", "aarch64-pc-windows-msvc"),
])
@pytest.mark.parametrize("bundled,directory", [
    (False, "bin"), (True, "bin"), (True, "codex"),
], ids=["optional-native-package", "bundled-native", "legacy-bundled-native"])
def test_windows_npm_codex_resolves_only_official_native_binary(
        tmp_path, monkeypatch, local, machine, package, triple, bundled, directory):
    base = tmp_path / "npm installation with spaces"
    shim = (base / "node_modules" / ".bin" if local else base) / "codex.cmd"
    shim.parent.mkdir(parents=True)
    # The launcher contains deliberately unrelated text: resolution must follow
    # the official package layout, without interpreting or executing the shim.
    shim.write_text("@echo UNTRUSTED-LAUNCHER\n", encoding="utf-8")
    packages = base / "node_modules" / "@openai"
    main_package = packages / "codex"
    main_package.mkdir(parents=True)
    vendor = (main_package if bundled else packages / package) / "vendor"
    binary = vendor / triple / directory / "codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native-fixture")
    monkeypatch.setattr(providers.shutil, "which", lambda value: str(shim))

    assert providers._resolve_codex_executable("codex", platform="win32", machine=machine) == str(binary.resolve())


def test_windows_explicit_exe_keeps_space_containing_path(tmp_path, monkeypatch):
    binary = tmp_path / "Program Files" / "Codex" / "codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native-fixture")
    monkeypatch.setattr(providers.shutil, "which", lambda value: str(binary))
    assert providers._resolve_codex_executable(str(binary), platform="win32") == str(binary.resolve())


@pytest.mark.parametrize("suffix", [".cmd", ".bat", ".ps1", ""])
def test_windows_launcher_without_native_package_has_actionable_error(tmp_path, monkeypatch, suffix):
    launcher = tmp_path / ("codex" + suffix)
    launcher.write_text("UNTRUSTED-LAUNCHER", encoding="utf-8")
    monkeypatch.setattr(providers.shutil, "which", lambda value: str(launcher))
    with pytest.raises(ProviderError) as error:
        providers._resolve_codex_executable("codex", platform="win32")
    assert error.value.code == "native_executable"
    assert "codex.exe" in str(error.value)
    assert "UNTRUSTED-LAUNCHER" not in str(error.value)


def test_missing_codex_keeps_not_installed_error(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda value: None)
    with pytest.raises(ProviderError) as error:
        providers._resolve_codex_executable("codex", platform="win32")
    assert error.value.code == "not_installed"


def test_windows_pnpm_package_link_resolves_optional_native_package(tmp_path, monkeypatch):
    packages = tmp_path / "node_modules"
    shim = packages / ".bin" / "codex.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("UNTRUSTED-LAUNCHER", encoding="utf-8")
    actual_packages = packages / ".pnpm" / "codex-fixture" / "node_modules" / "@openai"
    actual_package = actual_packages / "codex"
    actual_package.mkdir(parents=True)
    linked_package = packages / "@openai" / "codex"
    linked_package.parent.mkdir(parents=True)
    try:
        linked_package.symlink_to(actual_package, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"This filesystem cannot create pnpm directory links: {error}")
    binary = actual_packages / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin" / "codex.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native-fixture")
    monkeypatch.setattr(providers.shutil, "which", lambda value: str(shim))
    assert providers._resolve_codex_executable("codex", platform="win32", machine="AMD64") == str(binary.resolve())


@pytest.mark.parametrize("failure,code", [
    (NotImplementedError(), "native_runtime"),
    ("winerror193", "native_executable"),
])
def test_native_spawn_failures_are_actionable_without_platform_crash(monkeypatch, failure, code):
    if failure == "winerror193":
        failure = OSError("private local path or loader details")
        failure.winerror = 193

    async def fail_spawn(*args, **kwargs):
        raise failure

    monkeypatch.setattr(providers.asyncio, "create_subprocess_exec", fail_spawn)
    with pytest.raises(ProviderError) as error:
        asyncio.run(providers._spawn_codex_process("codex.exe", "app-server"))
    assert error.value.code == code
    assert "private local path" not in str(error.value)


@pytest.mark.parametrize("spawn_fails", [False, True])
def test_frozen_windows_spawn_filters_bundle_path_and_restores_dll_scope(tmp_path, monkeypatch, spawn_fails):
    bundle = tmp_path / "_MEI-fixture"
    installed = tmp_path / "user Codex"
    sibling = tmp_path / "_MEI-fixture-other"
    environment = {"PATH": os.pathsep.join(map(str, [bundle, bundle / "lib", installed, sibling])), "KEPT": "value"}
    original_path = environment["PATH"]
    calls = []
    result = object()

    @contextmanager
    def dll_scope():
        calls.append("dll-reset")
        try:
            yield
        finally:
            calls.append("dll-restored")

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        if spawn_fails:
            raise ProviderError("fixture process creation failed", "native_runtime")
        return result

    monkeypatch.setattr(providers.sys, "platform", "win32")
    monkeypatch.setattr(providers.sys, "frozen", True, raising=False)
    monkeypatch.setattr(providers.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(providers.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(providers, "_external_dll_directory", dll_scope)
    monkeypatch.setattr(providers, "_spawn_codex_process", spawn)

    if spawn_fails:
        with pytest.raises(ProviderError, match="fixture process creation failed"):
            asyncio.run(providers._spawn_codex("codex.exe", "--version", env=environment))
    else:
        assert asyncio.run(providers._spawn_codex("codex.exe", "--version", env=environment)) is result
    assert calls[0] == "dll-reset" and calls[-1] == "dll-restored"
    arguments, options = calls[1]
    assert arguments == ("codex.exe", "--version")
    assert options["creationflags"] == 0x08000000
    assert options["env"]["PATH"] == os.pathsep.join(map(str, [installed, sibling]))
    assert options["env"]["KEPT"] == "value"
    assert environment["PATH"] == original_path
