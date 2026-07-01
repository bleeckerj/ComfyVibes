"""Ordered tool argument normalization and preflight pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict


class ToolArgumentPreflight:
    _DIGEST_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9._-]+(?:\.json)?$")
    _RECENT_SIGNAL_TERMS_RE = re.compile(r"\b(last|latest|most\s+recent|newest)\b", re.IGNORECASE)
    _SIGNAL_TOKEN_RE = re.compile(r"\bsign[a-z]*\b", re.IGNORECASE)
    _EXPLICIT_WINDOW_RE = re.compile(
        r"\b(\d+)\s*(day|days|week|weeks|month|months|year|years)\b|"
        r"\b(today|yesterday|this\s+week|this\s+month)\b",
        re.IGNORECASE,
    )
    _ALL_TIME_RE = re.compile(r"\b(all[-\s]?time|since\s+forever|ever|from\s+forever)\b", re.IGNORECASE)
    _CREATE_INTENT_RE = re.compile(
        r"\b(create|add|ingest|capture|record)\b|\bnew\s+signal\b|\bad[-\s]?hoc\b",
        re.IGNORECASE,
    )
    _NUMBER_WORDS: dict[str, int] = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }

    def __init__(self, workflow_repairs: Any, binary_transfer: Any):
        self._workflow_repairs = workflow_repairs
        self._binary_transfer = binary_transfer

    async def prepare_tool_execution(
        self,
        orchestrator: Any,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> tuple[str, Dict[str, Any], Path | None]:
        arguments = await self._workflow_repairs.prepare_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        arguments = self._workflow_repairs.repair_workflows_import_from_artifact_arguments(
            tool_name,
            arguments,
            user_text=user_text,
        )
        await self._workflow_repairs.preflight_workflows_run_arguments(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        self._workflow_repairs.preflight_workflows_import_from_artifact_arguments(tool_name, arguments)
        self._normalize_editorial_content_write_arguments(tool_name, arguments)
        self._preflight_editorial_signal_lookup_arguments(tool_name, arguments)
        self._preflight_recent_signal_list_arguments(tool_name, arguments, user_text=user_text)
        self._preflight_editorial_signal_create_arguments(tool_name, user_text=user_text)
        transformed = await self._binary_transfer.maybe_convert_upload_url_to_from_path(
            orchestrator,
            tool_name,
            arguments,
            user_text=user_text,
        )
        if transformed is not None:
            exec_name, exec_args, temp_file = transformed
            exec_args = self._binary_transfer.apply_tanktracks_upload_conventions(
                exec_name,
                exec_args,
                user_text=user_text,
            )
            return exec_name, exec_args, temp_file
        arguments = self._binary_transfer.apply_tanktracks_upload_conventions(
            tool_name,
            arguments,
            user_text=user_text,
        )
        return tool_name, arguments, None

    @classmethod
    def _normalize_editorial_content_write_arguments(cls, tool_name: str, arguments: Dict[str, Any]) -> None:
        if tool_name == "editorial_content_create_stub":
            arguments.setdefault("profile", "practical")

        if tool_name not in {
            "editorial_content_apply_edits",
            "editorial_content_create_stub",
            "editorial_content_materialize_frontmatter_schema",
            "editorial_content_sync_frontmatter_schema",
        }:
            return

        if arguments.get("write") is not True:
            return
        arguments.setdefault("runChecks", True)
        arguments.setdefault("enforceChecks", True)

    @classmethod
    def _preflight_editorial_signal_lookup_arguments(cls, tool_name: str, arguments: Dict[str, Any]) -> None:
        # Both legacy `editorial_*` and canonical `digester_*` tool surfaces exist in the wild.
        # Treat them equivalently here so we can prevent common "digest id passed as signal id"
        # mistakes regardless of which server/tool name was selected.
        if tool_name not in {
            "digester_get_signal",
            "editorial_signals_get",
            "editorial_signals_get_text",
        }:
            return
        signal_id = cls._extract_signal_lookup_id(arguments)
        if not signal_id:
            return
        if not cls._looks_like_digest_identifier(signal_id):
            return
        raise RuntimeError(
            "Signal lookup preflight blocked: provided signal_id appears to be a digest id/path. "
            "Use digester_get_digest (or editorial_digests_get) to read the digest, then create a new signal via "
            "digester_signals_create."
        )

    @staticmethod
    def _extract_signal_lookup_id(arguments: Dict[str, Any]) -> str | None:
        for key in ("signal_id", "signalId", "id"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _looks_like_digest_identifier(cls, value: str) -> bool:
        candidate = value.strip()
        if not candidate:
            return False
        lowered = candidate.lower()
        if "/digests/" in lowered or lowered.startswith("digests/"):
            return True
        tail = Path(candidate).name
        return bool(cls._DIGEST_ID_PATTERN.match(tail))

    @classmethod
    def _preflight_recent_signal_list_arguments(
        cls,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        user_text: str,
    ) -> None:
        supported_tools = {
            # Canonical Digester tools.
            "digester_signals_list_summaries",
            "digester_get_story_queue",
            # Legacy/alternate tool surfaces.
            "editorial_signals_list_summaries",
            "editorial_signals_list",
            "editorial_get_story_queue",
            "get_story_queue",
        }
        if tool_name not in supported_tools:
            return

        text = (user_text or "").strip().lower()
        if not text:
            return
        if "digest" in text:
            return
        if not cls._RECENT_SIGNAL_TERMS_RE.search(text):
            return
        if not cls._SIGNAL_TOKEN_RE.search(text):
            return

        requested_limit = cls._extract_requested_recent_signal_count(text)
        existing_limit = arguments.get("limit")
        if requested_limit is None and isinstance(existing_limit, int) and existing_limit > 0:
            requested_limit = existing_limit
        if requested_limit is None:
            requested_limit = 3
        arguments["limit"] = max(1, min(int(requested_limit), 25))

        arguments["sort_by"] = "date"
        if "status" not in arguments:
            arguments["status"] = "all"
        if "min_potential" not in arguments and "minPotential" not in arguments:
            arguments["min_potential"] = 0

        has_explicit_window = bool(cls._EXPLICIT_WINDOW_RE.search(text))
        all_time_requested = bool(cls._ALL_TIME_RE.search(text))
        if all_time_requested or not has_explicit_window:
            # IMPORTANT: don't just drop `days` because many Digester list tools default to a 30-day
            # lookback. Set an explicit null to mean "no date window".
            arguments["days"] = None
            arguments.pop("start_date", None)
            arguments.pop("startDate", None)
            arguments.pop("end_date", None)
            arguments.pop("endDate", None)

    @classmethod
    def _extract_requested_recent_signal_count(cls, text: str) -> int | None:
        for match in re.finditer(
            r"\b(last|latest|most\s+recent|newest)\s+([a-z]+|\d{1,3})\b",
            text,
            flags=re.IGNORECASE,
        ):
            raw = match.group(2).lower()
            if raw.isdigit():
                return int(raw)
            if raw in cls._NUMBER_WORDS:
                return cls._NUMBER_WORDS[raw]

        for match in re.finditer(r"\b(\d{1,3})\s+sign[a-z]*\b", text, flags=re.IGNORECASE):
            return int(match.group(1))

        if re.search(r"\blast\s+signal\b", text, flags=re.IGNORECASE):
            return 1
        if re.search(r"\b(last|latest|most\s+recent|newest)\s+sign[a-z]*\b", text, flags=re.IGNORECASE):
            return 3
        return None

    @classmethod
    def _preflight_editorial_signal_create_arguments(cls, tool_name: str, *, user_text: str) -> None:
        create_tools = {
            # Canonical Digester tools.
            "digester_signals_create",
            "digester_create_ad_hoc_signal",
            # Legacy/alternate tool surfaces.
            "editorial_signals_create",
            "editorial_create_ad_hoc_signal",
            "create_ad_hoc_signal",
        }
        if tool_name not in create_tools:
            return
        text = (user_text or "").strip().lower()
        if not text:
            return

        retrieval_intent = bool(cls._RECENT_SIGNAL_TERMS_RE.search(text) and cls._SIGNAL_TOKEN_RE.search(text))
        explicit_create_intent = bool(cls._CREATE_INTENT_RE.search(text))
        if retrieval_intent and not explicit_create_intent:
            raise RuntimeError(
                "Signal-create preflight blocked: retrieval query detected ('last/latest signals'). "
                "Use digester_signals_list_summaries (preferred) or digester_get_story_queue for listing."
            )
