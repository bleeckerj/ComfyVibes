"""Track photarium namespace associations for image ids."""

from __future__ import annotations

from typing import Any


class ImageNamespaceRegistry:
    def __init__(self) -> None:
        self._image_namespace_by_id: dict[str, str] = {}

    def reset(self) -> None:
        self._image_namespace_by_id = {}

    def namespace_for(self, image_id: str) -> str | None:
        return self._image_namespace_by_id.get(image_id)

    def record_from_result(self, payload: Any) -> None:
        stack: list[Any] = [payload]
        while stack:
            current = stack.pop()
            if isinstance(current, list):
                stack.extend(current)
                continue
            if not isinstance(current, dict):
                continue
            image_id = self.extract_image_id(current)
            namespace = str(current.get("namespace") or "").strip()
            if image_id and namespace:
                self._image_namespace_by_id[image_id] = namespace
            stack.extend(current.values())

    @staticmethod
    def candidate_image_ids(payload: dict[str, Any]) -> list[str]:
        seen: set[str] = set()
        candidates: list[str] = []
        for key in ("imageId", "image_id", "parentId", "parent_id", "variantOf", "variant_of", "sourceImageId", "source_image_id", "inputImageId", "input_image_id"):
            value = payload.get(key)
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
        return candidates

    @staticmethod
    def extract_image_id(payload: dict[str, Any]) -> str | None:
        for key in ("image_id", "imageId", "catalog_image_id", "catalogImageId", "photarium_image_id", "photariumImageId", "image_uuid", "imageUuid", "uuid", "id"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None
