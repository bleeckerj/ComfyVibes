from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "manage_signal_extraction.py"
)
_SPEC = importlib.util.spec_from_file_location("manage_signal_extraction", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

_collect_status = _MODULE._collect_status
_expected_digest_path = _MODULE._expected_digest_path
_expected_signal_path = _MODULE._expected_signal_path
_resolve_digest_path = _MODULE._resolve_digest_path


def test_expected_signal_path_mirrors_digest_structure(tmp_path: Path) -> None:
    digests_root = tmp_path / "digests"
    signals_root = tmp_path / "signals"
    digest_path = digests_root / "2026" / "03" / "03082026_105127_digest-this.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("{}", encoding="utf-8")

    signal_path = _expected_signal_path(digest_path, digests_root, signals_root)
    assert signal_path == signals_root / "2026" / "03" / "signal_03082026_105127_digest-this.json"


def test_collect_status_counts_missing_and_extracted(tmp_path: Path) -> None:
    digests_root = tmp_path / "digests"
    signals_root = tmp_path / "signals"

    digest_one = digests_root / "2026" / "03" / "03082026_105127_digest-this.json"
    digest_two = digests_root / "2026" / "03" / "03082026_105128_digest-that.json"
    digest_one.parent.mkdir(parents=True, exist_ok=True)
    digest_one.write_text("{}", encoding="utf-8")
    digest_two.write_text("{}", encoding="utf-8")

    expected_signal_one = _expected_signal_path(digest_one, digests_root, signals_root)
    expected_signal_one.parent.mkdir(parents=True, exist_ok=True)
    expected_signal_one.write_text("{}", encoding="utf-8")

    status = _collect_status(digests_root, signals_root, days=None)
    assert status["digests_total"] == 2
    assert status["digests_with_signals"] == 1
    assert status["digests_missing_signals"] == 1
    assert status["missing"] == [digest_two]
    assert status["orphan_signals"] == []


def test_collect_status_reports_orphan_signals(tmp_path: Path) -> None:
    digests_root = tmp_path / "digests"
    signals_root = tmp_path / "signals"
    signal_path = signals_root / "2026" / "03" / "signal_03082026_105127_digest-this.json"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text("{}", encoding="utf-8")

    status = _collect_status(digests_root, signals_root, days=None)
    assert status["digests_total"] == 0
    assert status["digests_missing_signals"] == 0
    assert status["orphan_signals"] == [signal_path]


def test_expected_digest_path_mirrors_signal_structure(tmp_path: Path) -> None:
    digests_root = tmp_path / "digests"
    signals_root = tmp_path / "signals"
    signal_path = signals_root / "2026" / "03" / "signal_03082026_105127_digest-this.json"
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text("{}", encoding="utf-8")

    digest_path = _expected_digest_path(signal_path, digests_root, signals_root)
    assert digest_path == digests_root / "2026" / "03" / "03082026_105127_digest-this.json"


def test_resolve_digest_path_accepts_relative_to_digester_root(tmp_path: Path) -> None:
    digests_root = tmp_path / "digests"
    digest_path = digests_root / "2026" / "03" / "03082026_105127_digest-this.json"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("{}", encoding="utf-8")

    resolved = _resolve_digest_path("digests/2026/03/03082026_105127_digest-this.json", tmp_path, digests_root)
    assert resolved == digest_path.resolve()
