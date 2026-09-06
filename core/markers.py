"""ArUco ve QR fiducial üretimi (bkz. CLAUDE.md §2, §6.5).

Marker'lar **bit matrisi** olarak üretilir. Böylece PDF'e vektör kare olarak
çizilebilirler: baskıda kusursuz keskin, dosyada minik. Raster gerektiğinde
(önizleme, PNG çıktısı) aynı matris en yakın komşu ile büyütülür — asla
yumuşatılmaz, yoksa tarayıcı marker'ı okuyamaz.
"""

from __future__ import annotations

import functools
import logging
import re

import cv2
import numpy as np
from PIL import Image

from .constants import (
    CELL_MARKER_DICT,
    CORNER_MARKER_DICT,
    CORNER_MARKER_IDS,
    QR_PAYLOAD_PREFIX,
    QR_QUIET_ZONE_MODULES,
    SCHEMA_VERSION,
)
from .errors import MarkerDetectionFailed

log = logging.getLogger(__name__)

__all__ = [
    "aruco_bit_matrix",
    "qr_bit_matrix",
    "bit_matrix_to_image",
    "page_qr_payload",
    "parse_page_qr",
    "corner_marker_ids",
    "aruco_dictionary",
]

# ``DICT_5X5_100`` -> iç bit ızgarası 5, kenarlık 1 modül.
_DICT_GRID_RE = re.compile(r"DICT_(\d+)X\1_")


@functools.lru_cache(maxsize=8)
def aruco_dictionary(dict_name: str) -> cv2.aruco.Dictionary:
    """İsimden OpenCV ArUco sözlüğünü verir (önbelleklenir)."""
    try:
        predefined = getattr(cv2.aruco, dict_name)
    except AttributeError as exc:
        raise ValueError(f"Bilinmeyen ArUco sözlüğü: {dict_name}") from exc
    return cv2.aruco.getPredefinedDictionary(predefined)


def _grid_size(dict_name: str) -> int:
    """Sözlük adından iç bit ızgarasının kenar uzunluğunu çıkarır."""
    match = _DICT_GRID_RE.match(dict_name)
    if not match:
        raise ValueError(f"Sözlük adından ızgara boyutu okunamadı: {dict_name}")
    return int(match.group(1))


@functools.lru_cache(maxsize=512)
def aruco_bit_matrix(dict_name: str, marker_id: int, border_bits: int = 1) -> np.ndarray:
    """ArUco marker'ını bit matrisi olarak üretir.

    Returns:
        ``(n, n)`` boolean dizi; ``True`` siyah modül demektir. Kenarlık dahildir.
    """
    dictionary = aruco_dictionary(dict_name)
    side_modules = _grid_size(dict_name) + 2 * border_bits
    img = cv2.aruco.generateImageMarker(dictionary, int(marker_id), side_modules, border_bits)
    return (np.asarray(img) == 0).copy()


def qr_bit_matrix(payload: str, quiet_zone: int = QR_QUIET_ZONE_MODULES) -> np.ndarray:
    """QR kodunu bit matrisi olarak üretir; sessiz bölge kendimiz ekleriz.

    Returns:
        ``(n, n)`` boolean dizi; ``True`` siyah modül demektir.
    """
    encoder = cv2.QRCodeEncoder.create()
    encoded = encoder.encode(payload)
    if encoded is None or encoded.size == 0:  # pragma: no cover - kodlayıcı hatası
        raise MarkerDetectionFailed(f"QR kodu üretilemedi: {payload!r}")
    bits = np.asarray(encoded) == 0
    if quiet_zone > 0:
        bits = np.pad(bits, quiet_zone, mode="constant", constant_values=False)
    return bits


def bit_matrix_to_image(matrix: np.ndarray, side_px: int) -> Image.Image:
    """Bit matrisini keskin (en yakın komşu) bir gri tonlamalı görüntüye büyütür."""
    modules = matrix.shape[0]
    # Kenarların kaymaması için modül başına tam sayı piksel kullanılır.
    scale = max(1, side_px // modules)
    gray = np.where(matrix, 0, 255).astype(np.uint8)
    img = Image.fromarray(gray, mode="L").resize(
        (modules * scale, modules * scale), Image.Resampling.NEAREST
    )
    if img.width != side_px:
        img = img.resize((side_px, side_px), Image.Resampling.NEAREST)
    return img


def corner_marker_ids() -> tuple[int, int, int, int]:
    """Sayfa köşe marker'ları: sol-üst, sağ-üst, sağ-alt, sol-alt."""
    return CORNER_MARKER_IDS


# ---------------------------------------------------------------------------
# Sayfa QR yükü
# ---------------------------------------------------------------------------


def page_qr_payload(project_id: str, page_no: int, schema_version: int = SCHEMA_VERSION) -> str:
    """Sayfa QR içeriği: ``MM|mm_7f3a9c|1|1`` (CLAUDE.md §6.5)."""
    if "|" in project_id:
        raise ValueError("Proje kimliği '|' içeremez.")
    return f"{QR_PAYLOAD_PREFIX}|{project_id}|{page_no}|{schema_version}"


def parse_page_qr(text: str) -> tuple[str, int, int]:
    """Sayfa QR yükünü çözer.

    Returns:
        ``(project_id, page_no, schema_version)``

    Raises:
        MarkerDetectionFailed: Yük bu uygulamaya ait değilse veya bozuksa.
    """
    parts = text.strip().split("|")
    if len(parts) != 4 or parts[0] != QR_PAYLOAD_PREFIX:
        raise MarkerDetectionFailed(
            "Okunan QR bu uygulamaya ait bir sayfa kodu değil. "
            "Yanlış sayfa taranmış olabilir.",
            detail=f"Yük: {text!r}",
        )
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except ValueError as exc:
        raise MarkerDetectionFailed(
            "Sayfa QR kodu bozuk okundu. Taramayı tekrarlayın veya sayfa "
            "numarasını elle girin.",
            detail=f"Yük: {text!r}",
        ) from exc


# ---------------------------------------------------------------------------
# Kapasite kontrolleri
# ---------------------------------------------------------------------------


def cell_marker_capacity(dict_name: str = CELL_MARKER_DICT) -> int:
    """Sözlükteki toplam marker sayısı — sayfa başına hücre sayısının üst sınırı."""
    return int(aruco_dictionary(dict_name).bytesList.shape[0])


def validate_marker_budget(cells_per_page: int) -> None:
    """Sayfa başına hücre sayısı sözlüğe sığıyor mu diye bakar."""
    capacity = cell_marker_capacity()
    if cells_per_page > capacity:
        raise ValueError(
            f"Sayfa başına {cells_per_page} hücre, {CELL_MARKER_DICT} sözlüğünün "
            f"{capacity} marker kapasitesini aşıyor."
        )
    corner_capacity = cell_marker_capacity(CORNER_MARKER_DICT)
    if max(CORNER_MARKER_IDS) >= corner_capacity:  # pragma: no cover - sabit
        raise ValueError("Köşe marker kimlikleri sözlük kapasitesini aşıyor.")
