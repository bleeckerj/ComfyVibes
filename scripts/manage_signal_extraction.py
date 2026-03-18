#!/usr/bin/env python3
"""Deterministic manager for Digester digest->signal extraction workflows.

This script wraps /Users/julian/Code/Digester/signal_extractor.py so extraction
operations can be run from this repo with explicit modes:
- status / missing digests
- extract latest digests
- extract unprocessed digests (backfill)
- reprocess backfill
- extract one digest (ingest hook target) with verification
- images backfill (safe per-digest hydrate loop)
- optional watch mode for near-real-time ingestion follow-up
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


DEFAULT_DIGESTER_ROOT = Path("/Users/julian/Code/Digester")
_DIGEST_ID_RE = re.compile(r"^(?P<mm>\d{2})(?P<dd>\d{2})(?P<yyyy>\d{4})[_-](?P<hh>\d{2})(?P<mi>\d{2})(?P<ss>\d{2})")


def _iter_digest_files(digests_root: Path) -> Iterable[Path]:
    for path in sorted(digests_root.rglob("*.json")):
        if path.name.startswith("signal_") or path.name == "index.json":
            continue
        yield path


def _iter_signal_files(signals_root: Path) -> Iterable[Path]:
    for path in sorted(signals_root.rglob("signal_*.json")):
        if path.name == "index.json":
            continue
        yield path


def _parse_digest_timestamp(path: Path) -> dt.datetime:
    match = _DIGEST_ID_RE.match(path.stem)
    if match:
        parts = match.groupdict()
        try:
            return dt.datetime(
                int(parts["yyyy"]),
                int(parts["mm"]),
                int(parts["dd"]),
                int(parts["hh"]),
                int(parts["mi"]),
                int(parts["ss"]),
                tzinfo=dt.timezone.utc,
            )
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)


def _expected_signal_path(digest_path: Path, digests_root: Path, signals_root: Path) -> Path:
    rel = digest_path.resolve().relative_to(digests_root.resolve())
    return signals_root / rel.parent / f"signal_{rel.stem}.json"


def _expected_digest_path(signal_path: Path, digests_root: Path, signals_root: Path) -> Path | None:
    rel = signal_path.resolve().relative_to(signals_root.resolve())
    if not rel.name.startswith("signal_"):
        return None
    digest_name = rel.name[len("signal_") :]
    return digests_root / rel.parent / digest_name


def _filter_by_days(paths: list[Path], *, days: int | None) -> list[Path]:
    if not days or days <= 0:
        return paths
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return [path for path in paths if _parse_digest_timestamp(path) >= cutoff]


def _collect_status(digests_root: Path, signals_root: Path, *, days: int | None) -> dict:
    digests = list(_iter_digest_files(digests_root))
    digests = _filter_by_days(digests, days=days)
    missing: list[Path] = []
    extracted: list[Path] = []
    expected_signal_paths: set[Path] = set()
    for digest_path in digests:
        signal_path = _expected_signal_path(digest_path, digests_root, signals_root)
        expected_signal_paths.add(signal_path.resolve())
        if signal_path.exists():
            extracted.append(digest_path)
        else:
            missing.append(digest_path)
    orphan_signals: list[Path] = []
    for signal_path in _iter_signal_files(signals_root):
        digest_path = _expected_digest_path(signal_path, digests_root, signals_root)
        if digest_path is None or not digest_path.exists():
            orphan_signals.append(signal_path)
            continue
        if days and days > 0 and signal_path.resolve() not in expected_signal_paths:
            # Skip out-of-window non-orphans when a window filter is active.
            continue
    return {
        "digests_total": len(digests),
        "digests_with_signals": len(extracted),
        "digests_missing_signals": len(missing),
        "orphan_signals": orphan_signals,
        "missing": missing,
        "extracted": extracted,
    }


def _run_extractor(
    *,
    digester_root: Path,
    digester_python: Path,
    extractor_script: Path,
    config_path: Path,
    args: list[str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        str(digester_python),
        str(extractor_script),
        "--config",
        str(config_path),
        *args,
    ]
    return subprocess.run(
        command,
        cwd=str(digester_root),
        check=False,
        text=True,
        capture_output=capture_output,
    )


def _print_status_report(payload: dict, *, digests_root: Path, limit: int) -> None:
    print(f"Digests root: {digests_root}")
    print(f"Total digests scanned: {payload['digests_total']}")
    print(f"Digests with signals: {payload['digests_with_signals']}")
    print(f"Digests missing signals: {payload['digests_missing_signals']}")
    print(f"Orphan signals: {len(payload['orphan_signals'])}")
    missing: list[Path] = payload["missing"]
    if not missing:
        return
    print("\nMissing digest->signal mappings:")
    for path in missing[:limit]:
        print(f"- {path.relative_to(digests_root)}")
    if len(missing) > limit:
        print(f"- ... and {len(missing) - limit} more")


def _resolve_digest_path(raw_digest: str, digester_root: Path, digests_root: Path) -> Path:
    candidate = Path(raw_digest)
    if not candidate.is_absolute():
        candidate = (digester_root / raw_digest).resolve()
    else:
        candidate = candidate.resolve()
    if not candidate.exists():
        raise RuntimeError(f"Digest path not found: {candidate}")
    try:
        candidate.relative_to(digests_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"Digest path must be inside digests root ({digests_root}): {candidate}"
        ) from exc
    return candidate


def _require_paths(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    digests_root = root / "digests"
    signals_root = root / "signals"
    extractor_script = root / "signal_extractor.py"
    digester_python = root / ".venv" / "bin" / "python"
    config_path = root / "config.json"
    required = [digests_root, signals_root, extractor_script, digester_python, config_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing required Digester paths: {', '.join(missing)}")
    return digests_root, signals_root, extractor_script, digester_python, config_path


def _cmd_status(args: argparse.Namespace) -> int:
    digests_root, signals_root, _extractor_script, _digester_python, _config_path = _require_paths(args.digester_root)
    payload = _collect_status(digests_root, signals_root, days=args.days)
    if args.json:
        print(
            json.dumps(
                {
                    "digests_total": payload["digests_total"],
                    "digests_with_signals": payload["digests_with_signals"],
                    "digests_missing_signals": payload["digests_missing_signals"],
                    "orphan_signals": [
                        str(path.relative_to(signals_root))
                        for path in payload["orphan_signals"]
                    ],
                    "missing": [
                        str(path.relative_to(digests_root))
                        for path in payload["missing"]
                    ],
                },
                indent=2,
            )
        )
        return 0
    _print_status_report(payload, digests_root=digests_root, limit=args.limit)
    return 0


def _cmd_extract_latest(args: argparse.Namespace) -> int:
    _digests_root, _signals_root, extractor_script, digester_python, config_path = _require_paths(args.digester_root)
    extractor_args: list[str] = ["--days", str(args.days)]
    if args.reprocess:
        extractor_args.append("--reprocess")
    if args.list_only:
        extractor_args.append("--list")
    result = _run_extractor(
        digester_root=args.digester_root,
        digester_python=digester_python,
        extractor_script=extractor_script,
        config_path=config_path,
        args=extractor_args,
    )
    return result.returncode


def _cmd_extract_unprocessed(args: argparse.Namespace) -> int:
    _digests_root, _signals_root, extractor_script, digester_python, config_path = _require_paths(args.digester_root)
    extractor_args: list[str] = ["--backfill"]
    if args.days and args.days > 0:
        extractor_args.extend(["--max-age-days", str(args.days)])
    if args.list_only:
        extractor_args.append("--list")
    result = _run_extractor(
        digester_root=args.digester_root,
        digester_python=digester_python,
        extractor_script=extractor_script,
        config_path=config_path,
        args=extractor_args,
    )
    return result.returncode


def _cmd_extract_backfill(args: argparse.Namespace) -> int:
    _digests_root, _signals_root, extractor_script, digester_python, config_path = _require_paths(args.digester_root)
    extractor_args: list[str] = ["--backfill", "--reprocess"]
    if args.days and args.days > 0:
        extractor_args.extend(["--max-age-days", str(args.days)])
    if args.list_only:
        extractor_args.append("--list")
    result = _run_extractor(
        digester_root=args.digester_root,
        digester_python=digester_python,
        extractor_script=extractor_script,
        config_path=config_path,
        args=extractor_args,
    )
    return result.returncode


def _cmd_extract_digest(args: argparse.Namespace) -> int:
    digests_root, signals_root, extractor_script, digester_python, config_path = _require_paths(args.digester_root)
    digest_path = _resolve_digest_path(args.digest, args.digester_root, digests_root)
    extractor_args: list[str] = ["--digest", str(digest_path)]
    if args.hydrate_images:
        extractor_args.append("--hydrate-images")
    result = _run_extractor(
        digester_root=args.digester_root,
        digester_python=digester_python,
        extractor_script=extractor_script,
        config_path=config_path,
        args=extractor_args,
    )
    if result.returncode != 0 or args.skip_verify:
        return result.returncode
    expected_signal = _expected_signal_path(digest_path, digests_root, signals_root)
    if expected_signal.exists():
        print(f"Verified signal: {expected_signal}")
        return 0
    print(
        "Extractor completed but expected signal file was not found.\n"
        f"- digest: {digest_path}\n"
        f"- expected signal: {expected_signal}",
        file=sys.stderr,
    )
    return 3


def _cmd_images_backfill(args: argparse.Namespace) -> int:
    digests_root, signals_root, extractor_script, digester_python, config_path = _require_paths(args.digester_root)
    status_payload = _collect_status(digests_root, signals_root, days=args.days)
    digests = status_payload["extracted"]
    if args.limit and args.limit > 0:
        digests = digests[: args.limit]
    if not digests:
        print("No extracted digests selected for image hydration.")
        return 0

    print(f"Hydrating images for {len(digests)} extracted digests...")
    ok = 0
    failed = 0
    for index, digest_path in enumerate(digests, 1):
        rel = digest_path.relative_to(digests_root)
        print(f"[{index}/{len(digests)}] {rel}")
        result = _run_extractor(
            digester_root=args.digester_root,
            digester_python=digester_python,
            extractor_script=extractor_script,
            config_path=config_path,
            args=["--digest", str(digest_path), "--hydrate-images"],
            capture_output=True,
        )
        if result.returncode == 0:
            ok += 1
            if args.verbose and result.stdout.strip():
                print(result.stdout.rstrip())
        else:
            failed += 1
            print(f"  FAILED (exit {result.returncode})")
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
    print(f"Images backfill complete: {ok} succeeded, {failed} failed.")
    return 0 if failed == 0 else 1


def _cmd_watch(args: argparse.Namespace) -> int:
    print(
        "Watch mode running. This polls for recent unprocessed digests and runs extraction.\n"
        "Stop with Ctrl+C."
    )
    try:
        while True:
            code = _cmd_extract_latest(
                argparse.Namespace(
                    digester_root=args.digester_root,
                    days=args.days,
                    reprocess=False,
                    list_only=False,
                )
            )
            if code != 0:
                print(f"Extractor returned {code}; continuing after delay.", file=sys.stderr)
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\nStopped watch mode.")
        return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Digester digest->signal extraction workflows.")
    parser.add_argument(
        "--digester-root",
        type=Path,
        default=DEFAULT_DIGESTER_ROOT,
        help=f"Digester project root (default: {DEFAULT_DIGESTER_ROOT})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show digest->signal extraction coverage.")
    status.add_argument("--days", type=int, default=0, help="Restrict to digests from the last N days.")
    status.add_argument("--limit", type=int, default=100, help="Max missing digests to print.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    latest = subparsers.add_parser("extract-latest", help="Run extraction on latest digests.")
    latest.add_argument("--days", type=int, default=7, help="Look back window in days.")
    latest.add_argument("--reprocess", action="store_true", help="Force reprocess in the window.")
    latest.add_argument("--list-only", action="store_true", help="List selected digests without extracting.")

    unprocessed = subparsers.add_parser(
        "extract-unprocessed",
        help="Run extraction for digests without signals (backfill mode).",
    )
    unprocessed.add_argument("--days", type=int, default=0, help="Restrict to digests from the last N days.")
    unprocessed.add_argument("--list-only", action="store_true", help="List selected digests without extracting.")

    backfill = subparsers.add_parser(
        "extract-backfill",
        help="Reprocess historical digests (backfill + reprocess).",
    )
    backfill.add_argument("--days", type=int, default=0, help="Restrict to digests from the last N days.")
    backfill.add_argument("--list-only", action="store_true", help="List selected digests without extracting.")

    single = subparsers.add_parser(
        "extract-digest",
        help="Extract one digest and verify signal creation (for ingestion hooks).",
    )
    single.add_argument("--digest", required=True, help="Digest JSON path (absolute or relative to Digester root).")
    single.add_argument("--hydrate-images", action="store_true", help="Also hydrate images for this digest.")
    single.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip post-extract expected-signal verification.",
    )

    images = subparsers.add_parser(
        "images-backfill",
        help="Backfill/refresh signal images for extracted digests.",
    )
    images.add_argument("--days", type=int, default=0, help="Restrict to extracted digests from the last N days.")
    images.add_argument("--limit", type=int, default=0, help="Limit number of digests to hydrate (0 = all).")
    images.add_argument("--verbose", action="store_true", help="Print per-digest extractor output.")

    watch = subparsers.add_parser(
        "watch",
        help="Continuously run extract-latest for near-real-time post-ingest extraction.",
    )
    watch.add_argument("--days", type=int, default=1, help="Look back window per polling iteration.")
    watch.add_argument("--poll-seconds", type=int, default=120, help="Seconds between extraction passes.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "status":
            return _cmd_status(args)
        if args.command == "extract-latest":
            return _cmd_extract_latest(args)
        if args.command == "extract-unprocessed":
            return _cmd_extract_unprocessed(args)
        if args.command == "extract-backfill":
            return _cmd_extract_backfill(args)
        if args.command == "extract-digest":
            return _cmd_extract_digest(args)
        if args.command == "images-backfill":
            return _cmd_images_backfill(args)
        if args.command == "watch":
            return _cmd_watch(args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
