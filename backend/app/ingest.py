"""Bounded in-memory archive reader: never extract user-provided paths to disk."""
from __future__ import annotations
import hashlib
import io
import os
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from charset_normalizer import from_bytes

MAX_FILES = int(os.getenv("IPTOPO_MAX_FILES", "2500"))
MAX_FILE_BYTES = int(os.getenv("IPTOPO_MAX_FILE_BYTES", str(4 * 1024 * 1024)))
MAX_TOTAL_BYTES = int(os.getenv("IPTOPO_MAX_TOTAL_BYTES", str(64 * 1024 * 1024)))
MAX_ZIP_DEPTH = int(os.getenv("IPTOPO_MAX_ZIP_DEPTH", "3"))
MAX_UPLOAD_BYTES = MAX_TOTAL_BYTES + 2 * 1024 * 1024


@dataclass
class IngestResult:
    files: list = field(default_factory=list)
    results: list = field(default_factory=list)
    total_bytes: int = 0
    entries: int = 0
    hashes: set = field(default_factory=set)
    names: set = field(default_factory=set)


def safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if not name or not path.parts or len(name) > 512 or path.is_absolute() or ".." in path.parts or ":" in path.parts[0] or any(ord(x) < 32 for x in name):
        raise ValueError("归档包含不安全的文件路径")
    return str(path)


def decode_text(data: bytes) -> tuple[str, str]:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    if b"\x00" in data[:8192]:
        raise ValueError("非文本文件已跳过")
    try:
        return data.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        match = from_bytes(data).best()
        if not match or match.chaos > 0.1:
            raise ValueError("无法可靠识别文本编码")
        return str(match), match.encoding


def ingest_uploads(uploads: list[tuple[str, bytes]]) -> IngestResult:
    out = IngestResult()

    def fail(name, message, status="failed"):
        out.results.append({"filename": name, "parse_status": status, "status": status,
                            "diagnostics": [{"severity": "error" if status == "failed" else "info", "code": "import_input", "message": message}], "message": message})

    def visit(name: str, data: bytes, depth=0):
        try:
            name = safe_name(name)
            out.entries += 1
            if out.entries > MAX_FILES * 2:
                raise ValueError(f"归档项目数超过 {MAX_FILES * 2} 限制")
            if depth > MAX_ZIP_DEPTH:
                raise ValueError(f"ZIP 嵌套深度超过 {MAX_ZIP_DEPTH} 限制")
            if name.endswith(".DS_Store") or "__MACOSX" in PurePosixPath(name).parts:
                return
            archive = data.startswith(b"PK\x03\x04") or name.lower().endswith(".zip")
            if archive:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    if len(z.infolist()) > MAX_FILES * 2:
                        raise ValueError("ZIP 条目数超过限制")
                    for info in z.infolist():
                        if info.is_dir():
                            continue
                        child = f"{name}/{info.filename}"
                        try:
                            safe_name(info.filename)
                            if info.flag_bits & 1:
                                raise ValueError("不支持加密 ZIP")
                            if stat.S_ISLNK(info.external_attr >> 16):
                                raise ValueError("归档中的符号链接不受支持")
                            if info.file_size > MAX_TOTAL_BYTES or out.total_bytes + info.file_size > MAX_TOTAL_BYTES:
                                raise ValueError("ZIP 解压大小超过总量限制")
                            # Read with an actual-byte limit; advertised sizes are not trusted.
                            with z.open(info) as stream:
                                payload = stream.read(min(MAX_TOTAL_BYTES - out.total_bytes, MAX_TOTAL_BYTES) + 1)
                            out.total_bytes += len(payload)
                            if out.total_bytes > MAX_TOTAL_BYTES:
                                raise ValueError("实际解压数据超过总量限制")
                            visit(child, payload, depth + 1)
                        except (ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile) as exc:
                            fail(child, str(exc))
                        if out.entries > MAX_FILES * 2 or out.total_bytes >= MAX_TOTAL_BYTES:
                            break
                return
            if len(out.files) >= MAX_FILES:
                raise ValueError(f"配置文件数超过 {MAX_FILES} 限制")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError(f"单文件超过 {MAX_FILE_BYTES // 1024 // 1024} MiB 限制")
            if depth == 0:
                out.total_bytes += len(data)
                if out.total_bytes > MAX_TOTAL_BYTES:
                    raise ValueError("上传总量超过限制")
            content, encoding = decode_text(data)
            if not content.strip():
                fail(name, "空文件已跳过", "skipped")
                return
            digest = hashlib.sha256(data).hexdigest()
            if digest in out.hashes:
                fail(name, "相同内容已在此批次处理", "skipped")
                return
            if name in out.names:
                raise ValueError("同批次文件名重复，请分别导入或使用目录区分")
            out.hashes.add(digest)
            out.names.add(name)
            out.files.append({"filename": name, "content": content, "encoding": encoding, "hash": digest})
        except (ValueError, RuntimeError, NotImplementedError, zipfile.BadZipFile, UnicodeError) as exc:
            fail(name, str(exc))

    for filename, content in uploads:
        visit(filename, content)
    return out
