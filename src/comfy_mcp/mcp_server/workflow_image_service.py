"""Image inspection and ratio helpers for workflow services."""

from __future__ import annotations

import re
from math import gcd
from math import log
from pathlib import Path
from typing import Any


class WorkflowImageService:
    """Utilities for local image inspection and ratio normalization."""

    def image_info(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"Image file not found: {file_path}")

        width, height, fmt = self.probe_image_dimensions(path)
        if width <= 0 or height <= 0:
            raise ValueError(f"Could not determine image dimensions: {file_path}")

        ratio_float = width / height
        divisor = gcd(width, height) or 1
        ratio_simple = f"{width // divisor}:{height // divisor}"
        orientation = "square"
        if width > height:
            orientation = "landscape"
        elif height > width:
            orientation = "portrait"

        return {
            "file_path": str(path),
            "format": fmt,
            "width": width,
            "height": height,
            "aspect_ratio_float": ratio_float,
            "aspect_ratio_simple": ratio_simple,
            "orientation": orientation,
        }

    @staticmethod
    def probe_image_dimensions(path: Path) -> tuple[int, int, str]:
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as img:
                width, height = img.size
                fmt = str(getattr(img, "format", "") or "").upper() or "UNKNOWN"
                return int(width), int(height), fmt
        except Exception:
            pass

        raw = path.read_bytes()
        if len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n"):
            width = int.from_bytes(raw[16:20], "big")
            height = int.from_bytes(raw[20:24], "big")
            return width, height, "PNG"

        if len(raw) >= 10 and raw[:6] in {b"GIF87a", b"GIF89a"}:
            width = int.from_bytes(raw[6:8], "little")
            height = int.from_bytes(raw[8:10], "little")
            return width, height, "GIF"

        if len(raw) >= 4 and raw[:2] == b"\xff\xd8":
            width, height = WorkflowImageService.probe_jpeg_dimensions(raw)
            return width, height, "JPEG"

        raise ValueError("Unsupported image format (install Pillow for broader support).")

    @staticmethod
    def probe_jpeg_dimensions(raw: bytes) -> tuple[int, int]:
        sof_markers = {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }
        idx = 2
        size = len(raw)
        while idx < size:
            while idx < size and raw[idx] == 0xFF:
                idx += 1
            if idx >= size:
                break
            marker = raw[idx]
            idx += 1
            if marker in {0xD8, 0xD9}:
                continue
            if idx + 2 > size:
                break
            seg_len = int.from_bytes(raw[idx:idx + 2], "big")
            if seg_len < 2 or idx + seg_len > size:
                break
            if marker in sof_markers:
                if idx + 7 > size:
                    break
                height = int.from_bytes(raw[idx + 3:idx + 5], "big")
                width = int.from_bytes(raw[idx + 5:idx + 7], "big")
                if width > 0 and height > 0:
                    return width, height
                break
            idx += seg_len
        raise ValueError("JPEG dimensions not found")

    @staticmethod
    def normalize_ratio_token(value: str) -> str | None:
        match = re.search(r"(\d+)\s*:\s*(\d+)", value)
        if not match:
            return None
        width = int(match.group(1))
        height = int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return f"{width}:{height}"

    @classmethod
    def match_allowed_aspect_choice(cls, requested: str, allowed_aspects: list[str]) -> str | None:
        if not requested:
            return None
        requested_clean = requested.strip()
        if not requested_clean:
            return None
        for allowed in allowed_aspects:
            if requested_clean == str(allowed).strip():
                return str(allowed)
        requested_ratio = cls.normalize_ratio_token(requested_clean)
        if not requested_ratio:
            return None
        for allowed in allowed_aspects:
            allowed_ratio = cls.normalize_ratio_token(str(allowed))
            if allowed_ratio and allowed_ratio == requested_ratio:
                return str(allowed)
        return None

    @classmethod
    def nearest_allowed_aspect_choice(cls, source_ratio: str, allowed_aspects: list[str]) -> str | None:
        exact = cls.match_allowed_aspect_choice(source_ratio, allowed_aspects)
        if exact:
            return exact
        source_float = cls.ratio_to_float(source_ratio)
        if source_float is None:
            return None
        best_choice: str | None = None
        best_distance: float | None = None
        for allowed in allowed_aspects:
            allowed_ratio = cls.normalize_ratio_token(str(allowed))
            allowed_float = cls.ratio_to_float(allowed_ratio or "")
            if allowed_float is None or allowed_float <= 0:
                continue
            distance = abs(log(source_float) - log(allowed_float))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_choice = str(allowed)
        return best_choice

    @classmethod
    def ratio_to_float(cls, ratio_text: str) -> float | None:
        token = cls.normalize_ratio_token(ratio_text)
        if not token:
            return None
        width_text, height_text = token.split(":")
        width = int(width_text)
        height = int(height_text)
        if width <= 0 or height <= 0:
            return None
        return width / height
