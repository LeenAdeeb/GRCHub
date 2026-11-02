"""Acquisition layer: download or register an official document, hash it, store it, record metadata.

Originals are never overwritten. Each document is stored under its SHA-256, so a new version
becomes a new file and the previous one is preserved.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pymupdf
import yaml

SOURCES_FILE = Path(__file__).with_name("sources.yaml")
USER_AGENT = "GRCHub/0.1 (personal research project)"


def load_sources() -> dict[str, dict]:
    with SOURCES_FILE.open(encoding="utf-8") as f:
        return {s["id"]: s for s in yaml.safe_load(f)["sources"]}


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_identity(data: bytes, source: dict) -> None:
    """Refuse the file unless every identity phrase appears in the first 3 pages."""
    phrases = source.get("identity_check") or []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        head = " ".join(doc[i].get_text() for i in range(min(3, len(doc))))
    missing = [p for p in phrases if p not in head]
    if missing:
        raise ValueError(f"File does not look like '{source['id']}': missing {missing} in first pages")


def acquire(source_id: str, storage_dir: Path) -> dict:
    source = load_sources()[source_id]
    if source["status"] != "verified" or not source.get("download_url"):
        raise RuntimeError(
            f"Source '{source_id}' is '{source['status']}' and has no usable download URL. "
            f"Download it from {source['landing_page']} and run: "
            f"python -m backend.cli register-local {source_id} <file>"
        )
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60) as client:
        response = client.get(source["download_url"])
        response.raise_for_status()
    check_identity(response.content, source)
    return _store(source, response.content, storage_dir, retrieved_from=source["download_url"])


def register_local(source_id: str, file_path: Path, storage_dir: Path, expect_sha256: str | None = None) -> dict:
    """Register a manually downloaded document after identity (and optional hash) checks."""
    source = load_sources()[source_id]
    data = file_path.read_bytes()
    if expect_sha256 and sha256_of(data) != expect_sha256.lower():
        raise ValueError(f"SHA-256 mismatch: file is {sha256_of(data)}, expected {expect_sha256}")
    check_identity(data, source)
    return _store(source, data, storage_dir, retrieved_from=f"manual:{file_path.name}")


def _store(source: dict, data: bytes, storage_dir: Path, retrieved_from: str) -> dict:
    digest = sha256_of(data)
    target_dir = storage_dir / "originals" / source["id"]
    target_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = target_dir / f"{digest}.pdf"
    is_new = not pdf_path.exists()
    if is_new:
        pdf_path.write_bytes(data)
    record = {
        "source_id": source["id"],
        "framework": source["framework"],
        "version": source["version"],
        "retrieved_from": retrieved_from,
        "publication_date": source.get("publication_date"),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "size_bytes": len(data),
        "stored_path": str(pdf_path),
        "is_new_version": is_new,
    }
    with (target_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record
