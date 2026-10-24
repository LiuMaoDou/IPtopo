import io
import stat
import struct
import zipfile

import pytest

from backend.app import ingest

CONFIG = b"hostname Router\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.252\n!\n"


def archive(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        for name, data in entries:
            output.writestr(name, data)
    return stream.getvalue()


def test_mixed_archive_skips_metadata_binary_and_duplicate_content():
    zipped = archive([("router.cfg", CONFIG), ("nested/copy.cfg", CONFIG), ("__MACOSX/._router.cfg", b"meta"), (".DS_Store", b"meta"), ("image.bin", b"\0binary"), ("empty.txt", b" ")])
    result = ingest.ingest_uploads([("configs.zip", zipped)])
    assert len(result.files) == 1
    assert result.files[0]["filename"] == "configs.zip/router.cfg"
    assert sum(row["status"] == "skipped" for row in result.results) == 2
    assert any(row["filename"].endswith("image.bin") and row["status"] == "failed" for row in result.results)


@pytest.mark.parametrize("path", ["../escaped.cfg", "/tmp/escaped.cfg", "C:/escaped.cfg", "nested/../../escaped.cfg", "..\\escaped.cfg", "bad\nname.cfg", ".", "./"])
def test_unsafe_names_fail_without_aborting_other_files(path):
    result = ingest.ingest_uploads([(path, CONFIG), ("good.cfg", CONFIG + b"!good")])
    assert len(result.files) == 1
    assert result.files[0]["filename"] == "good.cfg"
    assert result.results[0]["status"] == "failed"


def test_zip_traversal_and_symlink_do_not_extract(tmp_path):
    link = zipfile.ZipInfo("shortcut")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    zipped = archive([("../escaped.cfg", CONFIG), (link, b"/etc/passwd"), ("good.cfg", CONFIG)])
    result = ingest.ingest_uploads([("configs.zip", zipped)])
    assert len(result.files) == 1
    assert len(result.results) == 2
    assert all(row["status"] == "failed" for row in result.results)
    assert not list(tmp_path.iterdir())


def test_truncated_zip_is_reported_and_next_upload_still_processed():
    zipped = archive([("config.cfg", CONFIG)])
    result = ingest.ingest_uploads([("truncated.zip", zipped[:25]), ("good.cfg", CONFIG)])
    assert len(result.files) == 1
    assert result.results[0]["status"] == "failed"


def test_encrypted_archive_entry_rejected_with_clear_diagnostic():
    zipped = bytearray(archive([("config.cfg", CONFIG)]))
    for magic, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = zipped.index(magic)
        flags = struct.unpack_from("<H", zipped, start + offset)[0]
        struct.pack_into("<H", zipped, start + offset, flags | 1)
    result = ingest.ingest_uploads([("encrypted.zip", bytes(zipped))])
    assert not result.files
    assert "加密" in result.results[0]["message"]


def test_nested_zip_depth_limit_and_config_count(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_ZIP_DEPTH", 1)
    zipped = archive([("inner.zip", archive([("nested.cfg", CONFIG)])), ("good.cfg", CONFIG)])
    result = ingest.ingest_uploads([("outer.zip", zipped)])
    assert len(result.files) == 1
    assert any("嵌套深度" in row["message"] for row in result.results)
    monkeypatch.setattr(ingest, "MAX_FILES", 2)
    result = ingest.ingest_uploads([(f"{i}.cfg", CONFIG + str(i).encode()) for i in range(3)])
    assert len(result.files) == 2
    assert result.results[0]["status"] == "failed"


def test_per_file_and_actual_total_limits(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_FILE_BYTES", len(CONFIG) - 1)
    result = ingest.ingest_uploads([("oversize.cfg", CONFIG)])
    assert not result.files
    assert result.results[0]["status"] == "failed"
    monkeypatch.setattr(ingest, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(ingest, "MAX_TOTAL_BYTES", 150)
    result = ingest.ingest_uploads([("configs.zip", archive([("a.cfg", CONFIG), ("b.cfg", CONFIG + b"!second")]))])
    assert len(result.files) == 1
    assert any("大小" in row["message"] or "总量" in row["message"] for row in result.results)


def test_utf16_decode_and_non_extension_content_recognition():
    result = ingest.ingest_uploads([("backup.2026", CONFIG.decode().encode("utf-16"))])
    assert len(result.files) == 1
    assert result.files[0]["encoding"] == "utf-16"
    assert "hostname Router" in result.files[0]["content"]


def test_different_contents_same_name_do_not_silently_overwrite():
    result = ingest.ingest_uploads([("same.cfg", CONFIG), ("same.cfg", CONFIG.replace(b"Router", b"Other"))])
    assert len(result.files) == 1
    assert any("文件名重复" in row["message"] for row in result.results)
