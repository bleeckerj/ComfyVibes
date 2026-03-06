"""Capability indexing over stored workflows."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from comfy_mcp.reasoning.models import CapabilityCard, CapabilityParam
from comfy_mcp.workflow_store.store import WorkflowStore


class CapabilityIndex:
    """Build capability cards from workflow store metadata and params."""

    _SEARCH_STOP_WORDS = {
        "a",
        "an",
        "and",
        "as",
        "can",
        "for",
        "i",
        "image",
        "images",
        "is",
        "me",
        "of",
        "please",
        "the",
        "to",
        "what",
        "workflow",
        "workflows",
        "with",
    }
    _IMAGE_EDIT_QUERY_TERMS = {
        "edit",
        "editing",
        "img2img",
        "inpaint",
        "mask",
        "modify",
        "replace",
        "retouch",
        "restyle",
        "variant",
        "variants",
        "variation",
        "variations",
    }
    _VARIATION_QUERY_TERMS = {
        "alternate",
        "alternates",
        "alternatives",
        "variation",
        "variations",
        "variant",
        "variants",
        "vary",
        "varied",
    }
    _IMAGE_EDIT_TAGS = {"image-edit", "img2img"}
    _VARIATION_TAGS = {"variation", "variations", "variant", "variants"}
    _IMAGE_EDIT_PREFERRED_WORKFLOWS = {
        "flux_2_klein_4B": 2.5,
    }
    _VARIATION_PREFERRED_WORKFLOWS = {
        "image_variation_maker": 4.5,
        "flux_2_klein_4B_image_to_image_variations": 3.0,
        "flux_2_klein_4B_variations": 2.5,
    }

    _heuristic_keys = (
        "use_cases",
        "strengths",
        "tradeoffs",
        "style",
        "mood",
        "io_contract",
        "examples",
        "notes",
    )

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    def list_cards(
        self,
        query: str = "",
        limit: int = 20,
        tags: Optional[List[str]] = None,
        include_params: bool = False,
    ) -> List[Dict[str, Any]]:
        needle = query.strip().lower()
        query_tokens = self._search_tokens(query)
        image_edit_intent = self._has_image_edit_intent(query)
        variation_intent = self._has_variation_intent(query)
        tag_filter = [tag.lower() for tag in (tags or [])]
        ranked: List[tuple[float, Dict[str, Any]]] = []

        for entry in self._store.list_entries():
            card = self.get_card(entry.workflow_id, include_params=include_params)

            if tag_filter:
                card_tags = [tag.lower() for tag in card["tags"]]
                if not any(tag in card_tags for tag in tag_filter):
                    continue

            blob = self._search_blob(card)
            matched_tokens = sum(1 for token in query_tokens if token in blob)
            exact_match = bool(needle) and needle in blob
            variation_boost = (
                self._variation_priority_boost(card)
                if variation_intent
                else 0.0
            )
            if needle and not exact_match and matched_tokens == 0:
                if not (variation_intent and variation_boost > 0):
                    continue

            score = 0.0
            if needle:
                score += 1.0 if exact_match else 0.0
                score += matched_tokens / max(1, len(query_tokens))
            if image_edit_intent:
                score += self._image_edit_priority_boost(card)
            if variation_intent:
                score += variation_boost
            ranked.append((score, card))

        ranked.sort(key=lambda item: (-item[0], item[1]["workflow_id"]))
        return [card for _, card in ranked[: max(1, limit)]]

    def get_card(self, workflow_id: str, include_params: bool = True) -> Dict[str, Any]:
        meta = self._store.read_meta(workflow_id) or {}
        params = self._store.read_params(workflow_id) or {}
        capability = self._build_card(workflow_id, meta, params)
        return capability.to_dict(include_params=include_params)

    def _build_card(
        self,
        workflow_id: str,
        meta: Dict[str, Any],
        params_payload: Dict[str, Any],
    ) -> CapabilityCard:
        param_items = params_payload.get("params", [])
        params: List[CapabilityParam] = []
        required_params: List[str] = []
        optional_params: List[str] = []

        if isinstance(param_items, list):
            for raw in param_items:
                if not isinstance(raw, dict):
                    continue
                item = self._parse_param(raw)
                if item is None:
                    continue
                params.append(item)
                if item.required:
                    required_params.append(item.name)
                else:
                    optional_params.append(item.name)

        return CapabilityCard(
            workflow_id=workflow_id,
            name=str(meta.get("name") or workflow_id),
            description=str(meta.get("description") or ""),
            tags=[str(tag) for tag in (meta.get("tags") or [])],
            has_params=bool(params),
            required_params=required_params,
            optional_params=optional_params,
            params=params,
            heuristics=self._extract_heuristics(meta),
        )

    def _parse_param(self, raw: Dict[str, Any]) -> Optional[CapabilityParam]:
        name = str(raw.get("name") or "").strip()
        if not name:
            return None

        constraints = raw.get("constraints")
        choices: List[Any] = []
        if isinstance(constraints, dict) and isinstance(constraints.get("choices"), list):
            choices = list(constraints["choices"])

        return CapabilityParam(
            name=name,
            param_type=str(raw.get("type") or "string"),
            required=bool(raw.get("required", False)),
            description=str(raw.get("description") or ""),
            default=raw.get("default"),
            choices=choices,
        )

    def _extract_heuristics(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        heuristics: Dict[str, Any] = {}
        for key in self._heuristic_keys:
            if key in meta:
                heuristics[key] = meta[key]
        return heuristics

    @staticmethod
    def _search_blob(card: Dict[str, Any]) -> str:
        heuristics = card.get("heuristics") or {}
        parts = [
            card.get("workflow_id", ""),
            card.get("name", ""),
            card.get("description", ""),
            " ".join(card.get("tags", [])),
            str(heuristics),
        ]
        return " ".join(str(part) for part in parts).lower()

    @classmethod
    def _search_tokens(cls, value: str) -> List[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
        raw_tokens = [token for token in normalized.split(" ") if token]
        filtered = [token for token in raw_tokens if token not in cls._SEARCH_STOP_WORDS]
        return filtered or raw_tokens

    @classmethod
    def _has_image_edit_intent(cls, query: str) -> bool:
        tokens = set(cls._search_tokens(query))
        if any(token in tokens for token in cls._IMAGE_EDIT_QUERY_TERMS):
            return True
        query_text = query.lower()
        return "image edit" in query_text or "edit image" in query_text

    @classmethod
    def _has_variation_intent(cls, query: str) -> bool:
        tokens = set(cls._search_tokens(query))
        if any(token in tokens for token in cls._VARIATION_QUERY_TERMS):
            return True
        query_text = query.lower()
        return "image variation" in query_text or "image variants" in query_text

    @classmethod
    def _image_edit_priority_boost(cls, card: Dict[str, Any]) -> float:
        boost = 0.0
        workflow_id = str(card.get("workflow_id") or "")
        tags = {str(tag).lower() for tag in card.get("tags", [])}
        required = [str(name).lower() for name in card.get("required_params", [])]
        optional = [str(name).lower() for name in card.get("optional_params", [])]
        params = set(required + optional)

        if tags.intersection(cls._IMAGE_EDIT_TAGS):
            boost += 1.5
        if any("image" in name for name in params):
            boost += 0.6
        if any("prompt" in name for name in params):
            boost += 0.4
        boost += cls._IMAGE_EDIT_PREFERRED_WORKFLOWS.get(workflow_id, 0.0)
        return boost

    @classmethod
    def _variation_priority_boost(cls, card: Dict[str, Any]) -> float:
        boost = 0.0
        workflow_id = str(card.get("workflow_id") or "")
        tags = {str(tag).lower() for tag in card.get("tags", [])}
        text_blob = (
            f"{workflow_id} {card.get('name', '')} {card.get('description', '')}".lower()
        )
        if tags.intersection(cls._VARIATION_TAGS):
            boost += 1.5
        if any(token in text_blob for token in ("variation", "variant", "vary")):
            boost += 0.6
        boost += cls._VARIATION_PREFERRED_WORKFLOWS.get(workflow_id, 0.0)
        return boost
