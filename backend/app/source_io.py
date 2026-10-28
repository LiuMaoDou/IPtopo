"""UTF-8 source storage without platform newline translation.

Decoding and parser normalization are handled by ingestion/parsers. Persisted
source text must retain its CRLF, LF and CR characters for exact restoration
and stable evidence line numbers on every host platform.
"""
from pathlib import Path


def read_source_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def write_source_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
