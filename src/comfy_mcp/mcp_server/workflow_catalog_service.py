"""Workflow catalog and search behavior."""

from __future__ import annotations

import re
from typing import Any

from comfy_mcp.reasoning.service import WorkflowReasoningService
from comfy_mcp.workflow_store.packaging import build_hints_template
from comfy_mcp.workflow_store.store import WorkflowStore


class WorkflowCatalogService:
    _SEARCH_STOP_WORDS = {
        "a", "an", "and", "can", "for", "i", "is", "me", "must", "my", "of",
        "params", "parameter", "parameters", "please", "provide", "show", "the",
        "what", "workflow", "workflows", "with", "you",
    }
    _IMAGE_EDIT_QUERY_TERMS = {
        "edit", "editing", "img2img", "inpaint", "mask", "modify", "replace",
        "retouch", "restyle", "sweep", "variant", "variation", "variations",
    }
    _VARIATION_QUERY_TERMS = {
        "alternate", "alternates", "alternatives", "variation", "variations",
        "variant", "variants", "vary", "varied",
    }
    _IMAGE_EDIT_TAGS = {"image-edit", "img2img"}
    _VARIATION_TAGS = {"variation", "variations", "variant", "variants"}
    _IMAGE_EDIT_PREFERRED_WORKFLOWS = {
        "flux_2_klein_4B_variations": 4.0,
        "flux_2_klein_4B": 2.5,
    }
    _VARIATION_PREFERRED_WORKFLOWS = {
        "image_variation_maker": 4.5,
        "flux_2_klein_4B_image_to_image_variations": 3.0,
        "flux_2_klein_4B_variations": 2.5,
    }

    def __init__(self, store: WorkflowStore, reasoning: WorkflowReasoningService) -> None:
        self._store = store
        self._reasoning = reasoning

    @classmethod
    def _normalize_search_text(cls, value: str) -> str:
        text = value.lower()
        text = re.sub(r"\btext\s*[-_\s]?(?:to|2)\s*[-_\s]?image\b", " txt2img text2image ", text)
        text = re.sub(r"\btxt\s*[-_\s]?2\s*[-_\s]?img\b", " txt2img text2image ", text)
        text = re.sub(r"\btxt2img\b", " txt2img text2image ", text)
        text = re.sub(r"\bt2i\b", " txt2img text2image ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _search_tokens(cls, value: str) -> list[str]:
        normalized = cls._normalize_search_text(value)
        raw_tokens = [token for token in normalized.split(" ") if token]
        filtered_tokens = [token for token in raw_tokens if token not in cls._SEARCH_STOP_WORDS]
        return filtered_tokens or raw_tokens

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
    def _image_edit_priority_boost(cls, *, workflow_id: str, name: str, description: str, tags: list[str]) -> float:
        boost = 0.0
        tag_set = {str(tag).lower() for tag in tags}
        if tag_set.intersection(cls._IMAGE_EDIT_TAGS):
            boost += 1.5
        text_blob = f"{workflow_id} {name} {description}".lower()
        if "edit" in text_blob:
            boost += 0.5
        boost += cls._IMAGE_EDIT_PREFERRED_WORKFLOWS.get(workflow_id, 0.0)
        return boost

    @classmethod
    def _variation_priority_boost(cls, *, workflow_id: str, name: str, description: str, tags: list[str]) -> float:
        boost = 0.0
        tag_set = {str(tag).lower() for tag in tags}
        if tag_set.intersection(cls._VARIATION_TAGS):
            boost += 1.5
        text_blob = f"{workflow_id} {name} {description}".lower()
        if any(token in text_blob for token in ("variation", "variant", "vary")):
            boost += 0.6
        boost += cls._VARIATION_PREFERRED_WORKFLOWS.get(workflow_id, 0.0)
        return boost

    @staticmethod
    def _token_matches(query_token: str, hay_tokens: set[str]) -> bool:
        if query_token in hay_tokens:
            return True
        if len(query_token) < 3:
            return False
        return any(token.startswith(query_token) or query_token.startswith(token) for token in hay_tokens)

    def list(self) -> dict[str, Any]:
        entries = self._store.list_entries()
        primary_root = self._store.root.resolve()
        items = []
        for entry in entries:
            meta = self._store.read_meta(entry.workflow_id) or {}
            params_count = 0
            if entry.has_params:
                params_payload = self._store.read_params(entry.workflow_id) or {}
                param_items = params_payload.get("params")
                if isinstance(param_items, list):
                    params_count = len(param_items)
            source_root = entry.root.parent.resolve()
            items.append({
                "id": entry.workflow_id,
                "name": str(meta.get("name") or entry.workflow_id),
                "description": str(meta.get("description") or ""),
                "tags": [str(t) for t in (meta.get("tags") or [])],
                "has_meta": entry.has_meta,
                "has_params": entry.has_params,
                "params_count": params_count,
                "source_root": str(source_root),
                "source_path": str(entry.root),
                "is_primary_root": source_root == primary_root,
            })
        return {
            "primary_root": str(primary_root),
            "extra_roots": [str(root) for root in self._store.roots[1:]],
            "workflows": items,
        }

    def search(self, query: str, limit: int = 20, tags: list[str] | None = None) -> dict[str, Any]:
        entries = self._store.list_entries()
        normalized_query = self._normalize_search_text(query)
        query_tokens = self._search_tokens(query)
        image_edit_intent = self._has_image_edit_intent(query)
        variation_intent = self._has_variation_intent(query)
        tag_filter = [tag.lower() for tag in (tags or [])]
        ranked_results: list[tuple[float, dict[str, Any]]] = []
        for entry in entries:
            meta = self._store.read_meta(entry.workflow_id) or {}
            name = str(meta.get("name") or entry.workflow_id)
            description = str(meta.get("description") or "")
            meta_tags = [str(tag) for tag in (meta.get("tags") or [])]
            if tag_filter and not any(tag.lower() in tag_filter for tag in meta_tags):
                continue
            haystack = " ".join([entry.workflow_id, name, description, " ".join(meta_tags)])
            normalized_haystack = self._normalize_search_text(haystack)
            haystack_tokens = set(normalized_haystack.split(" "))
            exact_match = bool(normalized_query) and normalized_query in normalized_haystack
            matched_tokens = sum(1 for token in query_tokens if self._token_matches(token, haystack_tokens))
            required_matches = 1 if len(query_tokens) <= 2 else max(2, (len(query_tokens) + 1) // 2)
            intent_boost = self._image_edit_priority_boost(
                workflow_id=entry.workflow_id, name=name, description=description, tags=meta_tags,
            ) if image_edit_intent else 0.0
            variation_boost = self._variation_priority_boost(
                workflow_id=entry.workflow_id, name=name, description=description, tags=meta_tags,
            ) if variation_intent else 0.0
            if not exact_match and matched_tokens < required_matches:
                if not ((image_edit_intent and intent_boost > 0) or (variation_intent and variation_boost > 0)):
                    continue
            score = 1.0 if exact_match else (matched_tokens / max(1, len(query_tokens)))
            if image_edit_intent:
                score += intent_boost
            if variation_intent:
                score += variation_boost
            ranked_results.append((score, {
                "id": entry.workflow_id,
                "name": name,
                "description": description,
                "tags": meta_tags,
                "has_meta": entry.has_meta,
                "has_params": entry.has_params,
            }))
        ranked_results.sort(key=lambda item: (-item[0], item[1]["id"]))
        results = [item[1] for item in ranked_results[: max(1, limit)]]
        return {"query": query, "count": len(results), "workflows": results}

    def capabilities_list(self, query: str = "", limit: int = 20, tags: list[str] | None = None, include_params: bool = False) -> dict[str, Any]:
        return self._reasoning.list_capabilities(query=query, limit=limit, tags=tags, include_params=include_params)

    def capabilities_get(self, workflow_id: str, include_params: bool = True) -> dict[str, Any]:
        return self._reasoning.get_capability(workflow_id, include_params=include_params)

    def get(self, workflow_id: str) -> dict[str, Any]:
        workflow = self._store.read_workflow(workflow_id)
        return {
            "workflow": workflow,
            "meta": self._store.read_meta(workflow_id),
            "params": self._store.read_params(workflow_id),
        }

    def params_get(self, workflow_id: str) -> dict[str, Any]:
        return {"params": self._store.read_params(workflow_id)}

    def package_template_get(self, workflow_id: str) -> dict[str, Any]:
        workflow = self._store.read_workflow(workflow_id)
        existing_meta = self._store.read_meta(workflow_id)
        template = build_hints_template(workflow_id, workflow, existing_meta=existing_meta)
        return {"workflow_id": workflow_id, "hints_template": template}
