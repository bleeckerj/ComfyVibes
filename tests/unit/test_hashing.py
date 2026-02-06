"""Tests for workflow hashing utilities."""

from pathlib import Path

from comfy_mcp.workflow_store.hashing import sha256_bytes, sha256_file, sha256_json


def test_sha256_bytes_is_deterministic(tmp_path: Path) -> None:
    """Hashing bytes should be deterministic for identical content."""
    print("Test: sha256_bytes should return the same digest for the same bytes.")
    payload = b"comfy"
    assert sha256_bytes(payload) == sha256_bytes(payload)


def test_sha256_file_hashes_file_contents(tmp_path: Path) -> None:
    """Hashing a file should change when file contents change."""
    print("Test: sha256_file should reflect file content changes.")
    file_path = tmp_path / "payload.txt"
    file_path.write_text("first", encoding="utf-8")
    first_hash = sha256_file(file_path)

    file_path.write_text("second", encoding="utf-8")
    second_hash = sha256_file(file_path)

    assert first_hash != second_hash, "Expected file hash to change after content change"


def test_sha256_json_is_order_invariant() -> None:
    """Hashing JSON should be order-invariant for dict keys."""
    print("Test: sha256_json should be order-invariant for dict keys.")
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}

    assert sha256_json(left) == sha256_json(right)
