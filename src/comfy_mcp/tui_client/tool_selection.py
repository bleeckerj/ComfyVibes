"""Tool selection heuristics for the chat orchestrator."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}
_DIGEST_HANDOFF_RECENT = {
    # Canonical Digester tools.
    "digester_digests_list",
    "digester_list_recent_digests",
    "digester_digests_get",
    "digester_get_digest",
    "digester_search_digests",
    # Legacy/alternate tool surfaces.
    "editorial_digests_list",
    "editorial_list_recent_digests",
    "editorial_digests_get",
    "editorial_get_digest",
    "editorial_search_digests",
}
_DIGEST_SIGNAL_RECENT = {
    # Canonical Digester tools.
    "digester_get_signal",
    "digester_search_signals",
    "digester_signals_list_summaries",
    "digester_get_story_queue",
    "digester_signals_create",
    "digester_create_ad_hoc_signal",
    # Legacy/alternate tool surfaces.
    "editorial_signals_get",
    "editorial_signals_get_text",
    "editorial_signals_search",
    "editorial_signals_search_text",
    "editorial_signals_list_summaries",
    "editorial_get_story_queue",
    "editorial_signals_create",
    "editorial_create_ad_hoc_signal",
}
_DIGEST_TERMS = ("digest", "digests")
_SIGNAL_TERMS = ("signal", "signals")
_LATEST_TERMS = ("latest", "recent", "newest", "last")
_EXTRACT_TERMS = (
    "extract",
    "extraction",
    "extractor",
    "backfill",
    "ingest",
    "ingested",
    "unextracted",
    "un-extracted",
    "hydrate",
)
_CONTENT_DRAFT_TOOLS = {
    "editorial_content_draft_from_brief",
    "editorial_content_create_stub",
    "editorial_content_apply_edits",
    "editorial_content_open_for_editing",
    "editorial_content_list",
}

_ISSUE_NUMBER_RE = re.compile(r"\bissue\s+\d+\b", re.IGNORECASE)


@dataclass(frozen=True)
class _ToolCapabilityCard:
    index: int
    tool: Dict[str, Any]
    name: str
    namespace: str
    token_pool: tuple[str, ...]


class ToolSelector:
    _MIN_INDEX_CANDIDATES = 64
    _MAX_INDEX_MULTIPLIER = 4
    _LEXICAL_SCORE_WEIGHT = 120.0

    def __init__(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = tools
        self._cards = self._build_capability_cards(tools)
        self._tool_index_by_name = {
            card.name: card.index
            for card in self._cards
            if card.name
        }
        self._token_to_tool_indexes, self._token_idf = self._build_lexical_index(self._cards)
        self.last_selection_debug: Dict[str, int] = {}

    def select_tools_for_user_text(
        self,
        user_text: str,
        recent_tool_names: set[str],
        max_tools_per_request: int,
        *,
        context_text: str | None = None,
    ) -> List[Dict[str, Any]]:
        if len(self._tools) <= max_tools_per_request:
            self.last_selection_debug = {
                "query_token_count": 0,
                "lexical_match_count": len(self._tools),
                "index_candidate_count": len(self._tools),
                "fallback_added_count": 0,
            }
            return self._tools

        text = self._build_selection_text(user_text=user_text, context_text=context_text)
        query_tokens = self._tokenize(text)
        lexical_scores = self._score_lexical_matches(query_tokens)
        candidate_indexes = self._retrieve_candidate_indexes(
            recent_tool_names=recent_tool_names,
            max_tools_per_request=max_tools_per_request,
            lexical_scores=lexical_scores,
        )
        ranked_candidate_indexes = self._rank_tool_indexes(
            indexes=candidate_indexes,
            text=text,
            recent_tool_names=recent_tool_names,
            lexical_scores=lexical_scores,
        )

        selected_indexes = list(ranked_candidate_indexes[:max_tools_per_request])
        fallback_added_count = 0
        if len(selected_indexes) < max_tools_per_request:
            selected_set = set(selected_indexes)
            remainder_indexes = [
                index
                for index in self._rank_tool_indexes(
                    indexes=list(range(len(self._tools))),
                    text=text,
                    recent_tool_names=recent_tool_names,
                    lexical_scores=lexical_scores,
                )
                if index not in selected_set
            ]
            for index in remainder_indexes:
                selected_indexes.append(index)
                selected_set.add(index)
                fallback_added_count += 1
                if len(selected_indexes) >= max_tools_per_request:
                    break

        self.last_selection_debug = {
            "query_token_count": len(query_tokens),
            "lexical_match_count": len(lexical_scores),
            "index_candidate_count": len(candidate_indexes),
            "fallback_added_count": fallback_added_count,
        }
        selected_indexes = self._ensure_pinned_tools_included(
            selected_indexes=selected_indexes[:max_tools_per_request],
            text=text,
            recent_tool_names=recent_tool_names,
            lexical_scores=lexical_scores,
            max_tools_per_request=max_tools_per_request,
        )
        self.last_selection_debug["pinned_included_count"] = max(
            0,
            len(self._collect_pinned_tool_indexes(text=text, recent_tool_names=recent_tool_names).intersection(set(selected_indexes))),
        )
        return [self._tools[index] for index in selected_indexes[:max_tools_per_request]]

    @staticmethod
    def _build_selection_text(*, user_text: str, context_text: str | None = None) -> str:
        context = (context_text or "").strip()
        user = (user_text or "").strip()
        if context and user:
            return f"{context}\n{user}".lower()
        return (context or user).lower()

    @staticmethod
    def tool_name(tool: Dict[str, Any]) -> str:
        return str(tool.get("function", {}).get("name", ""))

    @staticmethod
    def tool_priority(name: str, text: str, recent_tool_names: set[str]) -> int:
        if not name:
            return -10_000
        score = 0
        has_digest_intent = any(term in text for term in _DIGEST_TERMS)
        has_signal_intent = any(term in text for term in _SIGNAL_TERMS)
        has_extract_intent = any(term in text for term in _EXTRACT_TERMS)
        has_latest_intent = any(term in text for term in _LATEST_TERMS)
        has_explicit_create_intent = any(
            token in text for token in ("create", "add", "ingest", "record", "ad hoc", "ad-hoc")
        )
        has_feature_intent = "feature" in text or "features" in text
        has_issue_intent = bool(_ISSUE_NUMBER_RE.search(text))
        has_content_intent = has_feature_intent or has_issue_intent or "src/content" in text

        # Namespace bias: Digester tools should handle digest/signal retrieval. The legacy `editorial_*`
        # wrappers still exist, but we should not prefer them unless the user is explicitly in that context.
        if (has_digest_intent or has_signal_intent) and name.startswith("digester_"):
            score += 240
        if (
            (has_digest_intent or has_signal_intent)
            and name.startswith("editorial_")
            and ("digest" in name or "signal" in name)
            and "editorial" not in text
        ):
            score -= 80

        if has_digest_intent:
            if "digest" in name:
                score += 220
            if any(term in text for term in _LATEST_TERMS):
                if "list_recent_digests" in name or ("list" in name and "digest" in name):
                    score += 140

        if has_signal_intent and "signal" in name:
            score += 170

        # For “last/latest signals” queries we want list/search/get tooling, not create tooling.
        if has_signal_intent and has_latest_intent:
            if "signals_list_summaries" in name:
                score += 420
            elif "get_story_queue" in name:
                score += 340
            elif "search_signals" in name:
                score += 220

            if "summary" in text and "signals_list_summaries" in name:
                score += 120

            if ("signals_create" in name or "create_ad_hoc_signal" in name) and not has_explicit_create_intent:
                score -= 520

        # If the user is drafting something that clearly belongs in nfl-editorial content (feature/issue),
        # prefer the editorial content draft/stub/edit tools over lower-level drafter wrappers.
        if has_content_intent:
            if name in _CONTENT_DRAFT_TOOLS:
                score += 700
            if name in {"editorial_draft_generate", "digester_generate_draft"} or "draft_generate" in name:
                score -= 260

        if (has_digest_intent or has_signal_intent) and has_extract_intent:
            if "signals_create" in name or "create_ad_hoc_signal" in name:
                score += 260
            elif "digests_get" in name or "get_digest" in name:
                score += 180
            elif "signals_search" in name:
                score += 120

        if (has_digest_intent and has_signal_intent) and ("create" in name and "signal" in name):
            score += 140

        if "digester" in text and any(token in name for token in ("digest", "signal", "voice", "draft")):
            score += 120

        # Keep digest read/follow-up tools visible after a digest listing request
        # so short follow-up prompts (e.g. "summarize it") don't lose capability.
        if recent_tool_names.intersection(_DIGEST_HANDOFF_RECENT):
            if name in _DIGEST_HANDOFF_RECENT:
                score += 240
            elif name.startswith("editorial_") and "digest" in name:
                score += 140

        if recent_tool_names.intersection(_DIGEST_SIGNAL_RECENT):
            if name in _DIGEST_SIGNAL_RECENT:
                score += 110
            # Only keep create tooling sticky when the user is actually in a create flow.
            if ("signals_create" in name or "create_ad_hoc_signal" in name) and (
                has_extract_intent or has_explicit_create_intent
            ):
                score += 120

        if name in recent_tool_names:
            score += 120
        if name in {"list_tools", "tool_schema_get"}:
            score += 55
        if name in text:
            score += 300
        if name.startswith("workflows_"):
            score += 35
            if any(term in text for term in ("workflow", "corpus", "extract", "import", "package", "metadata")):
                score += 140
        elif name.startswith("comfy_"):
            score += 25
            if any(term in text for term in ("comfy", "comfyui", "queue", "history", "node", "model")):
                score += 120
        elif name.startswith("photarium_") or name.startswith("catalog_"):
            score += 20
            if any(term in text for term in ("photarium", "catalog", "image id", "namespace", "upload", "variant")):
                score += 120
        elif name.startswith("digester_"):
            score += 10
            if any(term in text for term in ("digester", "digest", "digests", "signal", "signals", "draft", "voice")):
                score += 120
        elif name.startswith("editorial_"):
            score += 10
            if any(term in text for term in ("editorial", "copy", "headline", "brief", "ad")):
                score += 120
        elif name.startswith("backoffice_"):
            score += 10
            if any(term in text for term in ("backoffice", "ticket", "crm", "finance")):
                score += 120
        return score

    @classmethod
    def _split_identifier_tokens(cls, raw_text: str) -> list[str]:
        expanded = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", raw_text.replace("_", " ").replace("-", " ").replace(".", " "))
        tokens = [match.group(0) for match in _TOKEN_RE.finditer(expanded.lower())]
        return [token for token in tokens if len(token) > 1 and token not in _QUERY_STOPWORDS]

    @classmethod
    def _tokenize(cls, text: str) -> list[str]:
        if not text:
            return []
        return cls._split_identifier_tokens(text)

    @classmethod
    def _build_capability_cards(cls, tools: List[Dict[str, Any]]) -> list[_ToolCapabilityCard]:
        cards: list[_ToolCapabilityCard] = []
        for index, tool in enumerate(tools):
            name = cls.tool_name(tool)
            function = tool.get("function", {}) if isinstance(tool, dict) else {}
            description = str(function.get("description", "") or "")
            parameters = function.get("parameters")
            parameter_names = cls._collect_parameter_names(parameters)

            namespace = ""
            if name:
                if "_" in name:
                    namespace = name.split("_", 1)[0]
                elif "." in name:
                    namespace = name.split(".", 1)[0]

            token_pool_set = set()
            token_pool_set.update(cls._split_identifier_tokens(name))
            token_pool_set.update(cls._split_identifier_tokens(description))
            token_pool_set.update(cls._split_identifier_tokens(namespace))
            for param_name in parameter_names:
                token_pool_set.update(cls._split_identifier_tokens(param_name))

            cards.append(
                _ToolCapabilityCard(
                    index=index,
                    tool=tool,
                    name=name,
                    namespace=namespace,
                    token_pool=tuple(sorted(token_pool_set)),
                )
            )
        return cards

    @classmethod
    def _collect_parameter_names(cls, schema: Any, *, max_depth: int = 2) -> list[str]:
        names: list[str] = []

        def _walk(node: Any, depth: int) -> None:
            if depth < 0 or not isinstance(node, dict):
                return

            properties = node.get("properties")
            if isinstance(properties, dict):
                for key, value in properties.items():
                    if isinstance(key, str) and key:
                        names.append(key)
                    _walk(value, depth - 1)

            items = node.get("items")
            if isinstance(items, dict):
                _walk(items, depth - 1)

            for composite_key in ("anyOf", "allOf", "oneOf"):
                composite = node.get(composite_key)
                if isinstance(composite, list):
                    for item in composite:
                        _walk(item, depth - 1)

        _walk(schema, max_depth)
        return names

    @classmethod
    def _build_lexical_index(
        cls, cards: list[_ToolCapabilityCard]
    ) -> tuple[dict[str, tuple[int, ...]], dict[str, float]]:
        token_to_indexes: dict[str, set[int]] = {}
        for card in cards:
            for token in card.token_pool:
                token_to_indexes.setdefault(token, set()).add(card.index)

        finalized_index: dict[str, tuple[int, ...]] = {
            token: tuple(sorted(indexes))
            for token, indexes in token_to_indexes.items()
        }

        tool_count = max(1, len(cards))
        token_idf: dict[str, float] = {}
        for token, indexes in finalized_index.items():
            token_idf[token] = 1.0 + math.log((1 + tool_count) / (1 + len(indexes)))
        return finalized_index, token_idf

    def _score_lexical_matches(self, query_tokens: list[str]) -> dict[int, float]:
        scores: dict[int, float] = {}
        for token in query_tokens:
            indexes = self._token_to_tool_indexes.get(token)
            if not indexes:
                continue
            weight = self._token_idf.get(token, 1.0)
            for index in indexes:
                scores[index] = scores.get(index, 0.0) + weight
        return scores

    def _retrieve_candidate_indexes(
        self,
        *,
        recent_tool_names: set[str],
        max_tools_per_request: int,
        lexical_scores: dict[int, float],
    ) -> list[int]:
        candidate_cap = min(
            len(self._tools),
            max(self._MIN_INDEX_CANDIDATES, max_tools_per_request * self._MAX_INDEX_MULTIPLIER),
        )
        candidates: list[int] = []
        seen: set[int] = set()

        ranked_lexical_indexes = sorted(
            lexical_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        for index, _score in ranked_lexical_indexes:
            if index in seen:
                continue
            seen.add(index)
            candidates.append(index)
            if len(candidates) >= candidate_cap:
                return candidates

        mandatory_names = set(recent_tool_names)
        mandatory_names.update({"list_tools", "tool_schema_get"})
        for name in mandatory_names:
            index = self._tool_index_by_name.get(name)
            if index is None or index in seen:
                continue
            seen.add(index)
            candidates.append(index)
            if len(candidates) >= candidate_cap:
                return candidates

        if len(candidates) >= max_tools_per_request:
            return candidates

        baseline_indexes = self._rank_tool_indexes(
            indexes=list(range(len(self._tools))),
            text="",
            recent_tool_names=recent_tool_names,
            lexical_scores={},
        )
        for index in baseline_indexes:
            if index in seen:
                continue
            seen.add(index)
            candidates.append(index)
            if len(candidates) >= candidate_cap:
                break
        return candidates

    def _rank_tool_indexes(
        self,
        *,
        indexes: list[int],
        text: str,
        recent_tool_names: set[str],
        lexical_scores: dict[int, float],
    ) -> list[int]:
        ranked: list[tuple[float, float, int, int]] = []
        for index in indexes:
            card = self._cards[index]
            priority = self.tool_priority(name=card.name, text=text, recent_tool_names=recent_tool_names)
            lexical = lexical_scores.get(index, 0.0)
            combined = float(priority) + (lexical * self._LEXICAL_SCORE_WEIGHT)
            ranked.append((combined, lexical, priority, index))

        ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        return [index for _, _, _, index in ranked]

    def _ensure_pinned_tools_included(
        self,
        *,
        selected_indexes: list[int],
        text: str,
        recent_tool_names: set[str],
        lexical_scores: dict[int, float],
        max_tools_per_request: int,
    ) -> list[int]:
        if not selected_indexes:
            return selected_indexes
        pinned_indexes = sorted(
            self._collect_pinned_tool_indexes(text=text, recent_tool_names=recent_tool_names)
        )
        if not pinned_indexes:
            return selected_indexes

        selected_set = set(selected_indexes)
        missing = [index for index in pinned_indexes if index not in selected_set]
        if not missing:
            return selected_indexes

        selected_ranked = self._rank_tool_indexes(
            indexes=selected_indexes,
            text=text,
            recent_tool_names=recent_tool_names,
            lexical_scores=lexical_scores,
        )
        removable = [index for index in reversed(selected_ranked) if index not in pinned_indexes]

        updated = list(selected_indexes)
        for pinned_index in missing:
            if pinned_index in selected_set:
                continue
            if len(updated) >= max_tools_per_request and removable:
                drop_index = removable.pop(0)
                if drop_index in updated:
                    updated.remove(drop_index)
                    selected_set.discard(drop_index)
            if len(updated) < max_tools_per_request:
                updated.append(pinned_index)
                selected_set.add(pinned_index)
        return updated

    def _collect_pinned_tool_indexes(self, *, text: str, recent_tool_names: set[str]) -> set[int]:
        pinned_names: set[str] = set()
        has_digest_intent = any(term in text for term in _DIGEST_TERMS)
        has_signal_intent = any(term in text for term in _SIGNAL_TERMS)
        has_editorial_context = "editorial" in text or bool(
            recent_tool_names.intersection(_DIGEST_HANDOFF_RECENT.union(_DIGEST_SIGNAL_RECENT))
        )
        has_extract_intent = any(term in text for term in _EXTRACT_TERMS)
        has_latest_intent = any(term in text for term in _LATEST_TERMS)
        has_explicit_create_intent = any(
            token in text for token in ("create", "add", "ingest", "record", "ad hoc", "ad-hoc")
        )
        has_feature_intent = "feature" in text or "features" in text
        has_issue_intent = bool(_ISSUE_NUMBER_RE.search(text))
        has_content_intent = has_feature_intent or has_issue_intent or "src/content" in text

        if not has_editorial_context and not has_digest_intent and not has_signal_intent:
            return set()

        if has_digest_intent or recent_tool_names.intersection(_DIGEST_HANDOFF_RECENT):
            pinned_names.update(
                {
                    # Canonical Digester tools.
                    "digester_digests_list",
                    "digester_list_recent_digests",
                    "digester_digests_get",
                    "digester_get_digest",
                    "digester_search_digests",
                    # Legacy/alternate tool surfaces.
                    "editorial_digests_list",
                    "editorial_list_recent_digests",
                    "editorial_digests_get",
                    "editorial_get_digest",
                    "editorial_search_digests",
                }
            )

        if has_signal_intent or recent_tool_names.intersection(_DIGEST_SIGNAL_RECENT):
            pinned_names.update(
                {
                    # Canonical Digester tools.
                    "digester_signals_list_summaries",
                    "digester_get_story_queue",
                    "digester_get_signal",
                    "digester_search_signals",
                    "digester_update_status",
                    # Legacy/alternate tool surfaces.
                    "editorial_signals_list_summaries",
                    "editorial_get_story_queue",
                    "editorial_signals_get",
                    "editorial_signals_get_text",
                    "editorial_signals_search",
                    "editorial_signals_search_text",
                    "editorial_signals_update_status",
                }
            )

            # Create tools should only be pinned when we're in a create/extract flow, not for simple
            # “last/latest signals” listing queries (those should use list/search tooling).
            if (has_extract_intent or has_explicit_create_intent) and not (has_latest_intent and not has_explicit_create_intent):
                pinned_names.update(
                    {
                        "digester_signals_create",
                        "digester_create_ad_hoc_signal",
                        "editorial_signals_create",
                        "editorial_create_ad_hoc_signal",
                    }
                )

        if has_content_intent:
            pinned_names.update(_CONTENT_DRAFT_TOOLS)

        indexes: set[int] = set()
        for name in pinned_names:
            index = self._tool_index_by_name.get(name)
            if index is not None:
                indexes.add(index)
        return indexes
