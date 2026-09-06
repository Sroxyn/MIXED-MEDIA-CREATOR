"""PDF üretimi, sayfa render'ı ve fiducial okunabilirliği testleri.

En önemlisi ``test_markers_survive_the_print_path``: basılı sayfadan
marker'ların geri okunabildiğini doğrular. M2'nin kabul koşulu budur
(bkz. CLAUDE.md §11) ve export boru hattının tamamı buna dayanır.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from mixedmedia.core import pdf_writer
from mixedmedia.core.constants import (
    CELL_MARKER_DICT,
    CORNER_MARKER_DICT,
    CORNER_MARKER_IDS,
)
from mixedmedia.core.errors import MixedMediaError
from mixedmedia.core.layout import mm_to_pt
from mixedmedia.core.markers import aruco_dictionary, parse_page_qr
from mixedmedia.core.models import Orientation, PaperSize
from mixedmedia.core.page_render import build_page_plan, render_page_raster

from .conftest import build_project

RENDER_DPI = 300


# ---------------------------------------------------------------------------
# Sayfa boyutu ve yapısı
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "paper,orientation,expect_w,expect_h",
    [
        (PaperSize.A4, Orientation.PORTRAIT, 595.28, 841.89),
        (PaperSize.A4, Orientation.LANDSCAPE, 841.89, 595.28),
        (PaperSize.A3, Orientation.PORTRAIT, 841.89, 1190.55),
        (PaperSize.A3, Orientation.LANDSCAPE, 1190.55, 841.89),
    ],
)
def test_pdf_mediabox_is_exact(tmp_path, paper, orientation, expect_w, expect_h):
    """MediaBox tam olarak kağıt ölçüsünde olmalı (CLAUDE.md §6.6)."""
    workspace, project = build_project(
        tmp_path / "p", frame_count=4, paper=paper, orientation=orientation
    )
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    with pymupdf.open(out) as doc:
        for page in doc:
            assert round(page.rect.width, 2) == expect_w
            assert round(page.rect.height, 2) == expect_h


def test_each_frame_is_a_separate_image_object(tmp_path):
    """Sayfa başına tek birleşik raster değil, kare başına ayrı görüntü (§6.6)."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    with pymupdf.open(out) as doc:
        assert doc.page_count == 2
        for page in doc:
            assert len(page.get_images()) == 4


def test_pdf_carries_project_id_in_metadata(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    with pymupdf.open(out) as doc:
        assert project.project_id in doc.metadata["subject"]
        assert doc.metadata["title"] == project.name


def test_cut_marks_and_labels_are_vector_not_raster(tmp_path):
    """Metin gerçekten metin olarak gömülmeli — vektör çizim koşulu."""
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    # Gömülü yazı tipinin ToUnicode eşlemesi ASCII "-" yerine U+2010 dönebilir;
    # baskıda görünen glif doğrudur, yalnız metin çıkarımını etkiler. Hücre
    # kimliğinin makine tarafı zaten ArUco marker'ıdır, bu metin insan içindir.
    text = text.replace("‐", "-")
    assert "p001-c00" in text
    assert "sayfa 1/" in text


def test_turkish_characters_survive_the_pdf(tmp_path):
    """Proje adı footer'a basılır; base-14 Helvetica Türkçeyi bozardı."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, name="Kedi Sekansı ğüş")
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    assert "Kedi Sekansı ğüş" in text


def test_missing_frame_gives_user_facing_error(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    (workspace.root / project.pages[0].cells[0].frame_file).unlink()

    with pytest.raises(MixedMediaError) as exc:
        pdf_writer.write_pdf(project, workspace.root, tmp_path / "out.pdf")
    assert "Kare dosyası bulunamadı" in str(exc.value)


def test_pdf_without_pages_is_refused(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    project.pages = []
    with pytest.raises(MixedMediaError):
        pdf_writer.write_pdf(project, workspace.root, tmp_path / "out.pdf")


# ---------------------------------------------------------------------------
# Fiducial okunabilirliği — M2 kabul koşulu
# ---------------------------------------------------------------------------


def _render_pdf_page_to_array(pdf_path: Path, page_no: int, dpi: int) -> np.ndarray:
    """PDF sayfasını gri tonlamalı numpy dizisine çevirir (taramayı taklit eder)."""
    with pymupdf.open(pdf_path) as doc:
        pix = doc[page_no].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def test_markers_survive_the_print_path(tmp_path):
    """Basılan sayfadan 4 köşe marker'ı, tüm hücre marker'ları ve QR okunabilmeli."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    page_gray = _render_pdf_page_to_array(out, 0, RENDER_DPI)

    corner_detector = cv2.aruco.ArucoDetector(aruco_dictionary(CORNER_MARKER_DICT))
    _, corner_ids, _ = corner_detector.detectMarkers(page_gray)
    assert corner_ids is not None, "Köşe marker'ı hiç bulunamadı"
    assert set(corner_ids.ravel().tolist()) == set(CORNER_MARKER_IDS)

    cell_detector = cv2.aruco.ArucoDetector(aruco_dictionary(CELL_MARKER_DICT))
    _, cell_ids, _ = cell_detector.detectMarkers(page_gray)
    assert cell_ids is not None, "Hücre marker'ı hiç bulunamadı"
    expected = {cell.marker_id for cell in project.pages[0].cells}
    assert expected.issubset(set(cell_ids.ravel().tolist()))

    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(page_gray)
    assert decoded, "Sayfa QR'ı okunamadı"
    project_id, page_no, schema_version = parse_page_qr(decoded)
    assert project_id == project.project_id
    assert page_no == 1
    assert schema_version == project.schema_version


def test_page_qr_identifies_each_page(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    for index in range(2):
        gray = _render_pdf_page_to_array(out, index, RENDER_DPI)
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(gray)
        assert parse_page_qr(decoded)[1] == index + 1


def test_markers_absent_when_disabled(tmp_path):
    """Marker'lar kapatılabilir olmalı — estetik gerekçesiyle (CLAUDE.md §2)."""
    workspace, project = build_project(
        tmp_path / "p", frame_count=4, markers_enabled=False, calibration_strip=False
    )
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    gray = _render_pdf_page_to_array(out, 0, RENDER_DPI)
    detector = cv2.aruco.ArucoDetector(aruco_dictionary(CORNER_MARKER_DICT))
    _, ids, _ = detector.detectMarkers(gray)
    assert ids is None or len(ids) == 0


# ---------------------------------------------------------------------------
# Raster render
# ---------------------------------------------------------------------------


def test_raster_render_matches_paper_size(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    plan = build_page_plan(project, project.pages[0], workspace.root)
    image = render_page_raster(plan, RENDER_DPI)

    assert image.size == (
        round(210 / 25.4 * RENDER_DPI),
        round(297 / 25.4 * RENDER_DPI),
    )
    assert image.mode == "RGB"


def test_page_pngs_are_written(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    written = pdf_writer.write_page_pngs(project, workspace.root, tmp_path / "png", dpi=150)
    assert [p.name for p in written] == ["page_001.png", "page_002.png"]
    assert all(p.stat().st_size > 0 for p in written)


def test_rotated_source_lays_out_in_display_orientation(tmp_path):
    """Dikey çekilmiş telefon videosu yan yatmamalı (CLAUDE.md §6.1)."""
    workspace, project = build_project(tmp_path / "p", frame_count=2, cols=1, rows=2)
    project.source.rotation = 90
    assert project.source.aspect_ratio < 1.0

    plan = build_page_plan(project, project.pages[0], workspace.root)
    images = [item for item in plan.items if hasattr(item, "loader")]
    assert images
    for item in images:
        assert item.rect.h_mm > item.rect.w_mm, "Dikey kare yatay kutuya yerleştirilmiş"


# ---------------------------------------------------------------------------
# Bellek — kareler asla topluca belleğe alınmaz (CLAUDE.md §10)
# ---------------------------------------------------------------------------


def _working_set_mb() -> float | None:
    """Süreç bellek kullanımı (MB). Windows dışında ``None``."""
    if sys.platform != "win32":
        return None

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("PageFaultCount", wt.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
        ] + [(name, ctypes.c_size_t) for name in "abcdef"]

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(_Counters),
        wt.DWORD,
    ]
    counters = _Counters()
    counters.cb = ctypes.sizeof(_Counters)
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None
    return counters.WorkingSetSize / (1024 * 1024)


@pytest.mark.skipif(sys.platform != "win32", reason="Bellek ölçümü Windows'a özgü")
def test_memory_stays_flat_across_many_pages(tmp_path):
    """Sayfa sayısı arttıkça bellek kullanımı doğrusal büyümemeli."""
    workspace, project = build_project(tmp_path / "p", frame_count=96, cols=2, rows=2)
    assert len(project.pages) == 24

    samples: list[float] = []
    pdf_writer.write_pdf(
        project,
        workspace.root,
        tmp_path / "out.pdf",
        progress=lambda _: samples.append(_working_set_mb() or 0.0),
    )

    first_quarter = sum(samples[:6]) / 6
    last_quarter = sum(samples[-6:]) / 6
    growth = last_quarter - first_quarter
    assert growth < 25, f"Bellek {growth:.0f} MB büyüdü — kareler birikiyor olabilir"


def test_large_document_stays_small(tmp_path):
    """24 sayfalık A4 belge makul boyutta kalmalı (hedef: < 120 MB, §6.6)."""
    workspace, project = build_project(tmp_path / "p", frame_count=96, cols=2, rows=2)
    out = tmp_path / "out.pdf"
    pdf_writer.write_pdf(project, workspace.root, out)

    size_mb = out.stat().st_size / (1024 * 1024)
    assert size_mb < 120, f"PDF {size_mb:.1f} MB"


def test_cancelled_run_leaves_no_partial_file(tmp_path):
    import threading

    workspace, project = build_project(tmp_path / "p", frame_count=96, cols=2, rows=2)
    out = tmp_path / "out.pdf"
    cancel = threading.Event()

    def on_progress(update):
        if update.page_done == 10:
            cancel.set()

    from mixedmedia.core.errors import OperationCancelled

    with pytest.raises(OperationCancelled):
        pdf_writer.write_pdf(
            project, workspace.root, out, progress=on_progress, cancel=cancel
        )
    assert not out.exists()


def test_pdf_points_conversion_is_exact():
    assert round(mm_to_pt(210.0), 2) == 595.28
    assert round(mm_to_pt(297.0), 2) == 841.89
    assert round(mm_to_pt(420.0), 2) == 1190.55
