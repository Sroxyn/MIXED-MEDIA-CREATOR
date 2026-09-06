"""Baskı için kare işleme (bkz. CLAUDE.md §6.3).

Mixed media'da sanatçı basılan karenin **üstüne** çizer; bu yüzden baskı açık
tonlu olmalıdır. ``print_density`` görüntüyü beyaza doğru harmanlar:
``out = 255 - (255 - img) * density``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .models import Processing, ProcessingMode

log = logging.getLogger(__name__)

__all__ = [
    "load_frame",
    "process_frame",
    "process_frame_file",
    "apply_print_density",
    "to_lineart",
    "to_halftone",
]

# 8×8 Bayer eşik matrisi — sıralı dither için.
_BAYER_8 = np.array(
    [
        [0, 32, 8, 40, 2, 34, 10, 42],
        [48, 16, 56, 24, 50, 18, 58, 26],
        [12, 44, 4, 36, 14, 46, 6, 38],
        [60, 28, 52, 20, 62, 30, 54, 22],
        [3, 35, 11, 43, 1, 33, 9, 41],
        [51, 19, 59, 27, 49, 17, 57, 25],
        [15, 47, 7, 39, 13, 45, 5, 37],
        [63, 31, 55, 23, 61, 29, 53, 21],
    ],
    dtype=np.float32,
)

_LINEART_BLOCK = 15
_LINEART_C = 7
_CANNY_LO, _CANNY_HI = 60, 160


def load_frame(path: Path) -> Image.Image:
    """Kareyi diskten okur. Her seferinde tek kare — akış zorunlu (CLAUDE.md §10)."""
    with Image.open(path) as img:
        return img.convert("RGB")


# ---------------------------------------------------------------------------
# Ton ayarları
# ---------------------------------------------------------------------------


def _apply_tone(arr: np.ndarray, p: Processing) -> np.ndarray:
    """Gama → kontrast → parlaklık sırasıyla ton ayarlarını uygular.

    ``gamma`` > 1 görüntüyü açar, < 1 koyultur. ``contrast`` 128 gri etrafında
    ölçekler. ``brightness`` -1..1 aralığında doğrudan eklenir.
    """
    out = arr.astype(np.float32)
    if p.gamma != 1.0:
        out = 255.0 * np.power(np.clip(out / 255.0, 0.0, 1.0), 1.0 / p.gamma)
    if p.contrast != 1.0:
        out = (out - 128.0) * p.contrast + 128.0
    if p.brightness != 0.0:
        out = out + p.brightness * 255.0
    return np.clip(out, 0.0, 255.0)


def apply_print_density(arr: np.ndarray, density: float) -> np.ndarray:
    """Görüntüyü beyaza doğru harmanlar. ``density=1`` orijinal, ``0`` tamamen beyaz."""
    return 255.0 - (255.0 - arr.astype(np.float32)) * float(density)


# ---------------------------------------------------------------------------
# Modlar
# ---------------------------------------------------------------------------


def to_lineart(gray: np.ndarray) -> np.ndarray:
    """Uyarlamalı eşik + Canny kenarlarıyla çizgi sanatı üretir (beyaz zemin, siyah çizgi)."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    threshed = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        _LINEART_BLOCK,
        _LINEART_C,
    )
    edges = cv2.Canny(blurred, _CANNY_LO, _CANNY_HI)
    return cv2.bitwise_and(threshed, cv2.bitwise_not(edges))


def to_halftone(gray: np.ndarray) -> np.ndarray:
    """8×8 Bayer sıralı dither ile yarım ton üretir."""
    h, w = gray.shape
    tiles = np.tile(_BAYER_8, (h // 8 + 1, w // 8 + 1))[:h, :w]
    thresholds = (tiles + 0.5) * (255.0 / 64.0)
    return np.where(gray.astype(np.float32) > thresholds, 255, 0).astype(np.uint8)


# ---------------------------------------------------------------------------
# Ana giriş noktası
# ---------------------------------------------------------------------------


def process_frame(img: Image.Image, p: Processing) -> Image.Image:
    """Bir kareyi baskı ayarlarına göre işler.

    Sıra: ton ayarları → mod dönüşümü → baskı yoğunluğu. Yoğunluk en sonda
    uygulanır ki ``lineart``/``halftone`` çıktısı da açık tonlu basılsın.

    Args:
        img: Kaynak kare (RGB).
        p: İşleme ayarları.

    Returns:
        ``original`` modunda RGB, diğerlerinde tek kanallı (``L``) görüntü.
    """
    arr = _apply_tone(np.asarray(img.convert("RGB")), p)

    if p.mode is ProcessingMode.ORIGINAL:
        out = arr
    else:
        gray = cv2.cvtColor(arr.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        if p.mode is ProcessingMode.GRAYSCALE:
            out = gray.astype(np.float32)
        elif p.mode is ProcessingMode.LINEART:
            out = to_lineart(gray).astype(np.float32)
        elif p.mode is ProcessingMode.HALFTONE:
            out = to_halftone(gray).astype(np.float32)
        else:  # pragma: no cover - enum kapalı küme
            raise ValueError(f"Bilinmeyen işleme modu: {p.mode}")

    if p.print_density != 1.0:
        out = apply_print_density(out, p.print_density)

    result = np.clip(out, 0.0, 255.0).astype(np.uint8)
    return Image.fromarray(result, mode="RGB" if result.ndim == 3 else "L")


def process_frame_file(path: Path, p: Processing) -> Image.Image:
    """Diskten okuyup işler — sayfa render'ının kullandığı tembel yükleyici."""
    return process_frame(load_frame(path), p)
