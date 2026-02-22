"""Search-tool argument and result normalization helpers.

These helpers add lightweight semantics for Photarium/catalog search tools:
- normalize color-search inputs to hex when users provide natural language colors
- expose image ids explicitly in search results (image_id + image_ids)
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse
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

_UPLOAD_NAME_KEYS: tuple[str, ...] = (
    "name",
    "title",
    "imageName",
    "image_name",
    "displayName",
    "display_name",
    "filename",
    "fileName",
    "file_name",
    "immutable_filename",
    "immutableFilename",
)
_UPLOAD_IMMUTABLE_KEYS: tuple[str, ...] = (
    "immutable_filename",
    "immutableFilename",
    "filename",
    "fileName",
    "file_name",
)
_UPLOAD_TEXT_CANDIDATE_KEYS: tuple[str, ...] = (
    "name",
    "title",
    "description",
    "caption",
    "prompt",
    "positive_prompt",
    "negative_prompt",
    "query",
    "brief",
    "subject",
    "concept",
    "style",
)
_UPLOAD_URL_CANDIDATE_KEYS: tuple[str, ...] = (
    "url",
    "imageUrl",
    "image_url",
    "view_url",
    "viewUrl",
)
_UPLOAD_LABEL_KEY_HINTS: tuple[str, ...] = (
    "name",
    "title",
    "label",
    "display",
    "filename",
    "slug",
    "immutable",
)
_UPLOAD_STOP_WORDS: set[str] = {
    "a",
    "an",
    "and",
    "api",
    "as",
    "at",
    "be",
    "been",
    "being",
    "by",
    "comfy",
    "comfyui",
    "do",
    "does",
    "doing",
    "done",
    "edit",
    "edited",
    "generate",
    "generated",
    "get",
    "got",
    "have",
    "i",
    "it",
    "its",
    "just",
    "let",
    "lets",
    "make",
    "me",
    "my",
    "now",
    "please",
    "file",
    "for",
    "from",
    "this",
    "that",
    "these",
    "those",
    "to",
    "up",
    "use",
    "using",
    "want",
    "we",
    "you",
    "your",
    "yours",
    "in",
    "into",
    "of",
    "on",
    "or",
    "output",
    "render",
    "temp",
    "the",
    "tmp",
    "upload",
    "view",
    "with",
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


def normalize_binary_transfer_arguments(
    tool_name: str,
    arguments: Dict[str, Any] | None,
    *,
    fallback_text: str | None = None,
) -> Dict[str, Any]:
    """Avoid large inline image payloads unless user explicitly requests them."""
    payload = dict(arguments or {})
    lowered = tool_name.lower()
    is_photarium = "photarium" in lowered or "catalog" in lowered
    if not is_photarium:
        return payload

    explicit_raw_request = _fallback_text_requests_raw_data(fallback_text)

    if "import_url" in lowered:
        payload = _force_flag_false_unless_explicit(
            payload,
            flag_keys=("includeData", "include_data", "includeBase64", "include_base64"),
            explicit_raw_request=explicit_raw_request,
        )
        return payload

    if "download_image" in lowered:
        payload = _force_flag_false_unless_explicit(
            payload,
            flag_keys=("includeBase64", "include_base64", "includeData", "include_data"),
            explicit_raw_request=explicit_raw_request,
        )
        return payload

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


def normalize_photarium_upload_arguments(
    tool_name: str,
    arguments: Dict[str, Any] | None,
    input_schema: Dict[str, Any] | None = None,
    fallback_text: str | None = None,
) -> Dict[str, Any]:
    """Ensure Photarium upload calls carry a sanitized semantic CamelCase name."""
    payload = dict(arguments or {})
    if not _is_photarium_upload_tool(tool_name):
        return payload

    # First, hard-fix any existing label-like fields that accidentally contain
    # URL/query transport blobs (e.g. view_filename=...&type=output...).
    semantic_fallback = _derive_upload_name_candidate(payload, name_key=None)
    blob_replacement = _to_camel_case_upload_label(
        semantic_fallback or "",
        drop_generic_media_tokens=True,
        filter_stop_words=True,
    )
    if not blob_replacement:
        blob_replacement = _semantic_label_from_fallback_text(fallback_text) or "UploadedImage"
    for key, raw in list(payload.items()):
        if not isinstance(raw, str):
            continue
        lowered_key = str(key).lower()
        if not any(hint in lowered_key for hint in _UPLOAD_LABEL_KEY_HINTS):
            continue
        if not _is_useless_query_blob_name(raw):
            continue
        payload[key] = _label_from_query_blob_name(raw) or blob_replacement

    name_key = _select_upload_name_key(payload, input_schema)
    if not name_key:
        return payload

    current = payload.get(name_key)
    if isinstance(current, str) and current.strip():
        if _is_useless_query_blob_name(current):
            blob_label = _label_from_query_blob_name(current)
            if blob_label:
                payload[name_key] = blob_label
                _propagate_upload_label_to_schema_fields(payload, input_schema, blob_label)
                return payload
        else:
            label = _to_camel_case_upload_label(
                current,
                drop_generic_media_tokens=False,
                filter_stop_words=False,
            )
            if label:
                payload[name_key] = label
                _propagate_upload_label_to_schema_fields(payload, input_schema, label)
                return payload

    candidate = _derive_upload_name_candidate(payload, name_key=name_key)
    label = _to_camel_case_upload_label(
        candidate or "",
        drop_generic_media_tokens=True,
        filter_stop_words=True,
    )
    if not label:
        label = _semantic_label_from_fallback_text(fallback_text)
    final_label = label or "UploadedImage"
    payload[name_key] = final_label
    _propagate_upload_label_to_schema_fields(payload, input_schema, final_label)
    return payload


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


def _select_upload_name_key(arguments: Dict[str, Any], input_schema: Dict[str, Any] | None) -> str | None:
    for key in _UPLOAD_NAME_KEYS:
        if key in arguments:
            return key

    if not isinstance(input_schema, dict):
        return None
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for key in _UPLOAD_NAME_KEYS:
        if key in properties:
            return key
    return None


def _derive_upload_name_candidate(arguments: Dict[str, Any], *, name_key: str | None = None) -> str | None:
    for key in _UPLOAD_TEXT_CANDIDATE_KEYS:
        if key == name_key:
            continue
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            if _is_useless_query_blob_name(value):
                continue
            return value

    tags = arguments.get("tags")
    if isinstance(tags, list):
        tag_values = [str(item).strip() for item in tags if str(item).strip()]
        if tag_values:
            return " ".join(tag_values[:3])

    for key in _UPLOAD_URL_CANDIDATE_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        url_label = _label_from_query_blob_name(value)
        if url_label:
            return url_label
    return None


def _propagate_upload_label_to_schema_fields(
    payload: Dict[str, Any],
    input_schema: Dict[str, Any] | None,
    label: str,
) -> None:
    if not isinstance(input_schema, dict):
        return
    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        return
    for key in _UPLOAD_NAME_KEYS:
        if key not in properties:
            continue
        if key in _UPLOAD_IMMUTABLE_KEYS:
            # Immutable/file-name style fields must always be clean pre-upload.
            payload[key] = label
            continue
        current = payload.get(key)
        if isinstance(current, str) and current.strip() and not _is_useless_query_blob_name(current):
            continue
        payload[key] = label


def _is_useless_query_blob_name(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    if "://" in text and ("?" in text or "&" in text):
        return True
    if "%3d" in text and "%26" in text:
        return True
    if "=" in text and "&" in text:
        keys = (
            "view_filename=",
            "filename=",
            "type=",
            "subfolder=",
            "file_path=",
            "filepath=",
            "savepath=",
            "save_path=",
            "localpath=",
            "local_path=",
        )
        if any(key in text for key in keys):
            return True
    return False


def _label_from_query_blob_name(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    parsed = urlparse(text)
    query_text = ""
    if parsed.scheme and parsed.query:
        query_text = parsed.query
    elif "=" in text and "&" in text:
        query_text = text.lstrip("?")

    if not query_text:
        return None

    query = parse_qs(query_text)
    filename_candidate: str | None = None
    for key in ("view_filename", "filename", "file", "name", "image"):
        matches = query.get(key)
        if not matches:
            continue
        raw = str(matches[0]).strip()
        if raw:
            filename_candidate = raw
            break

    if not filename_candidate:
        return None

    stem = unquote(filename_candidate)
    stem = re.sub(r"\.(?:png|jpe?g|webp|gif|bmp|tiff|avif)$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"(?:__|_)\d{5}_?$", "", stem)
    stem = re.sub(r"\d+x\d+", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"s\d+", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[0-9a-f]{8,}", " ", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem).strip(" _-")
    if not stem:
        return None
    return _to_camel_case_filename_label(stem)


def _to_camel_case_filename_label(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if not tokens:
        return None
    words: list[str] = []
    for token in tokens[:6]:
        if token.isdigit():
            words.append(token)
        else:
            words.append(token[0].upper() + token[1:].lower())
    label = "".join(words)
    return label[:60] if label else None


def _semantic_label_from_fallback_text(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if not tokens:
        return None
    meaningful = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _UPLOAD_STOP_WORDS:
            continue
        if len(token) <= 2 and not token.isdigit():
            continue
        meaningful.append(token)
    if len(meaningful) < 2:
        return None
    return _to_camel_case_upload_label(
        " ".join(meaningful[:6]),
        drop_generic_media_tokens=True,
        filter_stop_words=True,
    )


def _to_camel_case_upload_label(
    value: str,
    *,
    drop_generic_media_tokens: bool,
    filter_stop_words: bool,
) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    # Split existing CamelCase and delimiters into plain words first.
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    tokens = re.findall(r"[A-Za-z0-9]+", text)
    if not tokens:
        return None

    filtered: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if filter_stop_words and lowered in _UPLOAD_STOP_WORDS:
            continue
        if lowered in {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "avif"}:
            continue
        if re.fullmatch(r"[0-9a-f]{8,}", lowered):
            continue
        if token.isdigit() and len(token) >= 4:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        filtered.append(token)

    if not filtered:
        return None

    if drop_generic_media_tokens and len(filtered) > 1:
        filtered = [
            token
            for token in filtered
            if token.lower() not in {"image", "photo", "picture", "img"}
        ] or filtered

    words: list[str] = []
    for token in filtered[:6]:
        if token.isdigit():
            words.append(token)
        else:
            words.append(token[0].upper() + token[1:].lower())
    label = "".join(words)
    return label[:60] if label else None


def _normalize_hex(value: str) -> str:
    raw = value.strip().lower()
    if not raw.startswith("#"):
        raw = f"#{raw}"
    return raw


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _force_flag_false_unless_explicit(
    payload: Dict[str, Any],
    *,
    flag_keys: tuple[str, ...],
    explicit_raw_request: bool,
) -> Dict[str, Any]:
    normalized = dict(payload)
    if explicit_raw_request:
        return normalized

    found = False
    for key in flag_keys:
        if key not in normalized:
            continue
        found = True
        raw = normalized.get(key)
        if isinstance(raw, bool):
            normalized[key] = False
            continue
        if isinstance(raw, str):
            normalized[key] = False if raw.strip().lower() in {"1", "true", "yes", "on"} else raw
    if not found:
        # Prefer camelCase for explicit compatibility with existing MCP schemas.
        normalized[flag_keys[0]] = False
    return normalized


def _fallback_text_requests_raw_data(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return bool(
        re.search(
            r"\b(base64|data\s*url|include\s*data|include\s*base64|inline\s*data|raw\s*(bytes|payload|image))\b",
            text,
        )
    )


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


def _is_photarium_upload_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    if "upload" not in lowered:
        return False
    return "photarium" in lowered or "catalog" in lowered


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
