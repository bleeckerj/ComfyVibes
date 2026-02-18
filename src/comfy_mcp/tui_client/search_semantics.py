"""Search-tool argument and result normalization helpers.

These helpers add lightweight semantics for Photarium/catalog search tools:
- normalize color-search inputs to hex when users provide natural language colors
- expose image ids explicitly in search results (image_id + image_ids)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")
_HEX_ANYWHERE_RE = re.compile(r"#?[0-9a-fA-F]{6}")
_RGB_RE = re.compile(r"rgb\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*\)")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_COLOR_HEX_BY_NAME: Dict[str, str] = {
    "black": "#000000",
    "white": "#ffffff",
    "gray": "#808080",
    "grey": "#808080",
    "silver": "#c0c0c0",
    "red": "#ff0000",
    "maroon": "#800000",
    "orange": "#ffa500",
    "yellow": "#ffff00",
    "gold": "#ffd700",
    "olive": "#808000",
    "green": "#008000",
    "lime": "#00ff00",
    "teal": "#008080",
    "cyan": "#00ffff",
    "aqua": "#00ffff",
    "blue": "#0000ff",
    "navy": "#000080",
    "indigo": "#4b0082",
    "violet": "#8a2be2",
    "purple": "#800080",
    "magenta": "#ff00ff",
    "fuchsia": "#ff00ff",
    "pink": "#ffc0cb",
    "hot pink": "#ff69b4",
    "brown": "#8b4513",
    "tan": "#d2b48c",
    "beige": "#f5f5dc",
    "turquoise": "#40e0d0",
    "sky blue": "#87ceeb",
    "light blue": "#add8e6",
    "royal blue": "#4169e1",
    "forest green": "#228b22",
}


def normalize_search_tool_arguments(tool_name: str, arguments: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize search-oriented arguments before tool execution."""
    payload = dict(arguments or {})
    if not _is_color_search_tool(tool_name):
        return payload

    color_key = _select_color_key(payload)
    if not color_key:
        return payload

    color_value = payload.get(color_key)
    if not isinstance(color_value, str):
        return payload

    normalized_hex = semantic_color_to_hex(color_value)
    if not normalized_hex:
        return payload

    payload[color_key] = normalized_hex
    return payload


def normalize_search_tool_result(tool_name: str, result: Any) -> Any:
    """Expose image ids clearly in semantic-search style tool results."""
    if not _is_semantic_search_tool(tool_name):
        return result

    if isinstance(result, dict):
        image_ids: List[str] = []
        normalized = dict(result)
        for key in ("results", "images", "items", "matches", "hits", "data"):
            value = normalized.get(key)
            if isinstance(value, list):
                normalized[key] = [_normalize_item(item, image_ids) for item in value]
            elif isinstance(value, dict):
                normalized[key] = _normalize_item(value, image_ids)

        top_level_id = _extract_image_id(normalized)
        if top_level_id:
            normalized.setdefault("image_id", top_level_id)
            image_ids.append(top_level_id)

        deduped = _dedupe_strings(image_ids)
        if deduped:
            normalized["image_ids"] = deduped
            normalized.setdefault("primary_image_id", deduped[0])
        return normalized

    if isinstance(result, list):
        image_ids: List[str] = []
        normalized_items = [_normalize_item(item, image_ids) for item in result]
        return normalized_items

    return result


def semantic_color_to_hex(value: str) -> str | None:
    """Convert hex/rgb/color-name text to canonical #rrggbb when possible."""
    text = value.strip().lower()
    if not text:
        return None

    if _HEX_RE.fullmatch(text):
        return _normalize_hex(text)

    m_any_hex = _HEX_ANYWHERE_RE.search(text)
    if m_any_hex:
        return _normalize_hex(m_any_hex.group(0))

    m_rgb = _RGB_RE.search(text)
    if m_rgb:
        r, g, b = (max(0, min(255, int(v))) for v in m_rgb.groups())
        return _rgb_to_hex(r, g, b)

    compact = re.sub(r"[^a-z0-9 ]", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()
    if not compact:
        return None

    for name in sorted(_COLOR_HEX_BY_NAME.keys(), key=len, reverse=True):
        if name in compact:
            return _COLOR_HEX_BY_NAME[name]

    for token in compact.split(" "):
        hex_value = _COLOR_HEX_BY_NAME.get(token)
        if hex_value:
            return hex_value
    return None


def _normalize_item(item: Any, image_ids: List[str]) -> Any:
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    image_id = _extract_image_id(normalized)
    if image_id:
        normalized.setdefault("image_id", image_id)
        image_ids.append(image_id)
    return normalized


def _extract_image_id(payload: Dict[str, Any]) -> str | None:
    # Prefer canonical id fields over generic id/name fields.
    for key in (
        "image_id",
        "imageId",
        "catalog_image_id",
        "catalogImageId",
        "photarium_image_id",
        "photariumImageId",
        "image_uuid",
        "imageUuid",
        "uuid",
    ):
        candidate = _get_string(payload, key)
        if candidate:
            return candidate

    # Common nested containers from search payloads.
    for nested_key in ("image", "asset", "result", "metadata", "meta", "source"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in (
            "image_id",
            "imageId",
            "catalog_image_id",
            "catalogImageId",
            "photarium_image_id",
            "photariumImageId",
            "image_uuid",
            "imageUuid",
            "uuid",
        ):
            candidate = _get_string(nested, key)
            if candidate:
                return candidate

    # Fallback to generic id only if it looks like a true identifier.
    generic_id = _get_string(payload, "id")
    if generic_id and _is_likely_identifier(generic_id) and not _matches_display_name(payload, generic_id):
        return generic_id

    nested_image = payload.get("image")
    if isinstance(nested_image, dict):
        nested_generic = _get_string(nested_image, "id")
        if nested_generic and _is_likely_identifier(nested_generic) and not _matches_display_name(nested_image, nested_generic):
            return nested_generic
    return None


def _dedupe_strings(values: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _select_color_key(arguments: Dict[str, Any]) -> str | None:
    for key in ("hex", "color_hex", "rgb_hex", "color", "query"):
        if key in arguments:
            return key
    return None


def _normalize_hex(value: str) -> str:
    raw = value.strip().lower()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    return raw


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _is_color_search_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    if lowered.endswith("search_color") or "search_color" in lowered:
        return True
    return "color" in lowered and "search" in lowered


def _is_semantic_search_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    if lowered in {"photarium_similar", "photarium_antipode"}:
        return True
    if lowered.startswith(("photarium_search", "catalog_search")):
        return True
    if ("photarium" in lowered or "catalog" in lowered) and any(
        token in lowered for token in ("semantic", "search", "similar", "query", "find")
    ):
        return True
    return False


def _get_string(payload: Dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _matches_display_name(payload: Dict[str, Any], candidate: str) -> bool:
    cand_norm = candidate.strip().lower()
    for key in ("name", "display_name", "displayName", "title", "filename", "fileName"):
        raw = payload.get(key)
        if raw is None:
            continue
        if str(raw).strip().lower() == cand_norm:
            return True
    return False


def _is_likely_identifier(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if _UUID_RE.fullmatch(text):
        return True
    if _ULID_RE.fullmatch(text):
        return True
    if re.fullmatch(r"\d{6,}", text):
        return True
    if text.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".avif")):
        return False
    if "/" in text or "\\" in text:
        return False
    if " " in text:
        return False
    if re.fullmatch(r"[A-Za-z]+(?:[-_][A-Za-z]+)*", text):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{4,}", text))
