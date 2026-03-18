from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def _parse_roots(raw: str | None) -> list[Path]:
    if raw is None:
        raw = ""
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        # Reasonable default for this workspace.
        parts = ["/Users/julian/Code"]
    roots: list[Path] = []
    for part in parts:
        try:
            roots.append(Path(part).expanduser().resolve())
        except Exception:
            continue
    # De-dup
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


@dataclass(frozen=True)
class WorkspacePolicy:
    roots: list[Path]

    @classmethod
    def from_env(cls) -> "WorkspacePolicy":
        return cls(roots=_parse_roots(os.environ.get("WORKSPACE_MCP_ROOTS")))

    def ensure_allowed(self, path: Path) -> Path:
        resolved = path.expanduser().resolve(strict=False)
        for root in self.roots:
            try:
                if resolved.is_relative_to(root):
                    return resolved
            except Exception:
                continue
        root_list = ", ".join(str(root) for root in self.roots)
        raise RuntimeError(f"Path not allowed: {resolved}. Allowed roots: {root_list}")


def _resolve_user_path(policy: WorkspacePolicy, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        # Treat relative paths as relative to the first allowed root.
        if not policy.roots:
            raise RuntimeError("No workspace roots configured.")
        candidate = policy.roots[0] / candidate
    return policy.ensure_allowed(candidate)


def _stat_payload(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "path": str(path),
        "is_dir": path.is_dir(),
        "is_file": path.is_file(),
        "bytes": int(st.st_size),
        "mtime": float(st.st_mtime),
    }


class WorkspaceFsTools:
    """Filesystem tools scoped to allowlisted roots.

    NOTE: We intentionally don't provide raw shell access. All operations are validated against roots.
    """

    def __init__(self, policy: WorkspacePolicy | None = None) -> None:
        self._policy = policy or WorkspacePolicy.from_env()

    def workspace_roots(self) -> dict[str, Any]:
        return {"roots": [str(root) for root in self._policy.roots]}

    def workspace_path_stat(self, path: str) -> dict[str, Any]:
        resolved = _resolve_user_path(self._policy, path)
        if not resolved.exists():
            return {"found": False, "path": str(resolved)}
        return {"found": True, **_stat_payload(resolved)}

    def workspace_folder_list(
        self,
        path: str,
        *,
        recursive: bool = False,
        limit: int = 200,
    ) -> dict[str, Any]:
        resolved = _resolve_user_path(self._policy, path)
        if not resolved.exists() or not resolved.is_dir():
            raise RuntimeError(f"Not a directory: {resolved}")
        limit = max(1, min(int(limit), 5000))
        entries: list[dict[str, Any]] = []
        if recursive:
            iterator: Iterable[Path] = (p for p in resolved.rglob("*"))
        else:
            iterator = (p for p in resolved.iterdir())
        for item in iterator:
            try:
                if item.is_symlink():
                    # Avoid traversing symlinks that could point outside roots.
                    continue
                entries.append(_stat_payload(item))
            except Exception:
                continue
            if len(entries) >= limit:
                break
        return {"path": str(resolved), "count": len(entries), "items": entries}

    def workspace_file_read(self, path: str, *, max_bytes: int = 200_000) -> dict[str, Any]:
        resolved = _resolve_user_path(self._policy, path)
        if not resolved.exists() or not resolved.is_file():
            raise RuntimeError(f"Not a file: {resolved}")
        max_bytes = max(1, min(int(max_bytes), 2_000_000))
        data = resolved.read_bytes()
        truncated = False
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        try:
            text = data.decode("utf-8")
            encoding = "utf-8"
        except Exception:
            text = data.decode("utf-8", errors="replace")
            encoding = "utf-8+replace"
        return {
            "path": str(resolved),
            "encoding": encoding,
            "truncated": truncated,
            "content": text,
        }

    def workspace_file_write(
        self,
        path: str,
        *,
        content: str,
        mode: str = "overwrite",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        resolved = _resolve_user_path(self._policy, path)
        mode = (mode or "overwrite").strip().lower()
        if mode not in {"overwrite", "append", "create"}:
            raise RuntimeError("mode must be one of: overwrite, append, create")
        if mode == "create" and resolved.exists():
            raise RuntimeError(f"File already exists: {resolved}")
        if resolved.exists() and not resolved.is_file():
            raise RuntimeError(f"Not a file: {resolved}")
        parent = resolved.parent
        self._policy.ensure_allowed(parent)
        payload = {"path": str(resolved), "dry_run": bool(dry_run), "mode": mode}
        if dry_run:
            payload["bytes"] = len((content or "").encode("utf-8"))
            return payload
        parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with resolved.open("a", encoding="utf-8") as f:
                f.write(content or "")
        else:
            resolved.write_text(content or "", encoding="utf-8")
        payload["bytes"] = resolved.stat().st_size
        return payload

    def workspace_file_copy(
        self,
        src: str,
        dst: str,
        *,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        src_path = _resolve_user_path(self._policy, src)
        dst_path = _resolve_user_path(self._policy, dst)
        if not src_path.exists() or not src_path.is_file():
            raise RuntimeError(f"Not a file: {src_path}")
        if dst_path.exists() and not overwrite:
            raise RuntimeError(f"Destination exists: {dst_path}")
        if dst_path.exists() and not dst_path.is_file():
            raise RuntimeError(f"Destination is not a file: {dst_path}")
        payload = {"src": str(src_path), "dst": str(dst_path), "dry_run": bool(dry_run)}
        if dry_run:
            return payload
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        payload["bytes"] = dst_path.stat().st_size
        return payload

    def workspace_file_move(
        self,
        src: str,
        dst: str,
        *,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        src_path = _resolve_user_path(self._policy, src)
        dst_path = _resolve_user_path(self._policy, dst)
        if not src_path.exists() or not src_path.is_file():
            raise RuntimeError(f"Not a file: {src_path}")
        if dst_path.exists() and not overwrite:
            raise RuntimeError(f"Destination exists: {dst_path}")
        if dst_path.exists() and not dst_path.is_file():
            raise RuntimeError(f"Destination is not a file: {dst_path}")
        payload = {"src": str(src_path), "dst": str(dst_path), "dry_run": bool(dry_run)}
        if dry_run:
            return payload
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if dst_path.exists() and overwrite:
            dst_path.unlink()
        shutil.move(str(src_path), str(dst_path))
        payload["bytes"] = dst_path.stat().st_size
        return payload

    def workspace_file_delete(self, path: str, *, dry_run: bool = False) -> dict[str, Any]:
        resolved = _resolve_user_path(self._policy, path)
        if not resolved.exists():
            return {"path": str(resolved), "deleted": False, "reason": "not_found", "dry_run": bool(dry_run)}
        if resolved.is_dir():
            raise RuntimeError(f"Refusing to delete directory: {resolved}")
        if not resolved.is_file():
            raise RuntimeError(f"Not a file: {resolved}")
        if dry_run:
            return {"path": str(resolved), "deleted": False, "dry_run": True}
        resolved.unlink()
        return {"path": str(resolved), "deleted": True, "dry_run": False}

