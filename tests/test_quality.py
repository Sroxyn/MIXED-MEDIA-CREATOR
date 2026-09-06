"""Tarama kalitesi korumaları.

Gerçek bir kullanıcı taramasında ortaya çıkan iki kayıp burada kilitlenir:

1. **Gölgelerin ezilmesi.** Yazıcı, kalibrasyon şeridinin en koyu iki
   kademesini ayırt edemiyordu (0 ve 36 seviyeleri kağıtta aynı tona
   basılıyordu). Eğri bunu tersine çevirmeye çalışınca 2 birimlik bir ölçüm
   farkı 36 birimlik çıkışa yayılıyor, o bölgede tarayıcı gürültüsü katlanarak
   büyüyordu.
2. **Çözünürlüğün çöpe gitmesi.** Çalışma çözünürlüğü sabit 300 DPI'dı;
   1200 DPI'lık bir tarama daha ilk adımda dörtte birine indiriliyordu.
"""

from __future__ import annotations

import numpy as np
import pytest

from mixedmedia.core.constants import MAX_WORK_DPI, MIN_WORK_DPI
from mixedmedia.core.frame_normalize import _curve, apply_lut
from mixedmedia.core.models import Orientation, PaperSize
from mixedmedia.core.pipeline import recommended_work_dpi

from .conftest import build_project

# Kullanıcının yazıcısında ölçülen gerçek şerit yanıtı: en koyu iki kademe
# ayırt edilemiyor (46.7 ve 48.7), en parlak ikisi de birbirine çok yakın.
MEASURED = np.array([46.7, 48.7, 81.0, 133.3, 182.7, 221.7, 245.7, 255.0])
EXPECTED = np.array([0.0, 36.0, 73.0, 109.0, 146.0, 182.0, 219.0, 255.0])


def _gain(curve: np.ndarray, low: int, high: int) -> float:
    return (curve[high] - curve[low]) / (high - low)


# ---------------------------------------------------------------------------
# Kalibrasyon eğrisi
# ---------------------------------------------------------------------------


def test_indistinguishable_patches_do_not_create_a_steep_curve():
    """Ayırt edilemeyen kademeler gürültüyü büyüten dik bir parça üretmemeli."""
    curve = _curve(MEASURED, EXPECTED)
    assert curve is not None
    # Ham hâlinde 46.7 -> 0 ve 48.7 -> 36 olurdu: 2 birimde 36 birim, yani 18x.
    assert _gain(curve, 47, 60) < 2.0, f"gölgede kazanç yüksek: {_gain(curve, 47, 60):.2f}"


def test_curve_keeps_paper_white_at_white():
    """Kazanç sınırı beyaz noktayı aşağı çekmemeli; yoksa her sayfa griye kaçar."""
    curve = _curve(MEASURED, EXPECTED)
    assert curve[255] >= 250


def test_curve_is_monotonic():
    curve = _curve(MEASURED, EXPECTED)
    assert np.all(np.diff(curve) >= -1e-9)


def test_curve_does_not_crush_shadows_to_zero():
    """Baskının tutabildiği en koyu ton 0'a ezilmemeli.

    Yazıcının siyahı taramada ~47 okunuyor; oraya kadar her şeyi 0 yapmak
    gölgedeki tüm detayı yok eder.
    """
    curve = _curve(MEASURED, EXPECTED)
    assert curve[47] > 5, "baskının siyahı sıfıra eziliyor"


def test_a_well_behaved_printer_is_left_alone():
    """Yazıcı kademeleri düzgün ayırıyorsa eğri neredeyse birim olmalı."""
    levels = np.array([0.0, 36.0, 73.0, 109.0, 146.0, 182.0, 219.0, 255.0])
    curve = _curve(levels, levels)
    ramp = np.arange(256, dtype=np.float64)
    assert np.abs(curve - ramp).max() < 2.0


def test_curve_applied_to_an_image_preserves_shadow_separation():
    """Farklı koyu tonlar düzeltmeden sonra da birbirinden ayrı kalmalı."""
    curve = _curve(MEASURED, EXPECTED)
    lut = curve.astype(np.uint8).reshape(1, 256, 1)

    shadows = np.array([[[50, 55, 60, 65, 70]]], dtype=np.uint8).reshape(1, 5, 1)
    corrected = apply_lut(np.repeat(shadows, 3, axis=2), lut)[0, :, 0]
    assert len(set(corrected.tolist())) >= 4, f"gölgeler tek tona düştü: {corrected}"


# ---------------------------------------------------------------------------
# Çalışma çözünürlüğü
# ---------------------------------------------------------------------------


def test_work_dpi_grows_with_the_output_resolution(tmp_path):
    """Büyük çıktı daha yüksek çalışma çözünürlüğü ister."""
    _, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)

    project.export.resolution = (640, 360)
    small = recommended_work_dpi(project)
    project.export.resolution = (3840, 2160)
    large = recommended_work_dpi(project)

    assert large > small


def test_work_dpi_stays_within_bounds(tmp_path):
    _, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    for resolution in [(160, 90), (1280, 720), (7680, 4320)]:
        project.export.resolution = resolution
        assert MIN_WORK_DPI <= recommended_work_dpi(project) <= MAX_WORK_DPI


def test_work_dpi_supersamples_beyond_the_bare_minimum(tmp_path):
    """Halftone deseni son indirmede ortalansın diye hedefin üstünde çalışılır."""
    _, project = build_project(tmp_path / "p", frame_count=1, cols=1, rows=1)
    project.export.resolution = (640, 360)

    from mixedmedia.core.layout import fit_image_box, grid_from_settings

    geometry = grid_from_settings(project.layout)
    box = fit_image_box(geometry.cell_at(0), project.source.aspect_ratio)
    bare_minimum = 640 / (box.w_mm / 25.4)

    assert recommended_work_dpi(project) > bare_minimum


@pytest.mark.parametrize(
    "paper,orientation", [(PaperSize.A4, Orientation.PORTRAIT), (PaperSize.A3, Orientation.LANDSCAPE)]
)
def test_work_dpi_is_defined_for_every_layout(tmp_path, paper, orientation):
    _, project = build_project(
        tmp_path / "p", frame_count=4, cols=2, rows=2, paper=paper, orientation=orientation
    )
    assert MIN_WORK_DPI <= recommended_work_dpi(project) <= MAX_WORK_DPI


def test_denser_grids_need_more_working_resolution(tmp_path):
    """Küçük hücre, aynı çıktı için daha yüksek DPI ister."""
    _, sparse = build_project(tmp_path / "a", frame_count=1, cols=1, rows=1)
    _, dense = build_project(tmp_path / "b", frame_count=12, cols=3, rows=4)
    for project in (sparse, dense):
        project.export.resolution = (1280, 720)
    assert recommended_work_dpi(dense) >= recommended_work_dpi(sparse)


# ---------------------------------------------------------------------------
# PDF taramaları kendi çözünürlüğünün üstünde işlenmemeli
# ---------------------------------------------------------------------------


def _scan_like_pdf(path, dpi: int, pages: int = 1) -> None:
    """Tek, sayfayı kaplayan görüntüden oluşan bir PDF — gerçek tarama gibi."""
    import io

    import pymupdf
    from PIL import Image

    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595.28, height=841.89)
        width = round(210 / 25.4 * dpi)
        height = round(297 / 25.4 * dpi)
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
        page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(str(path))
    doc.close()


def test_a_scanned_pdf_is_not_rendered_above_its_own_resolution(tmp_path):
    """Taramanın kendi çözünürlüğünün üstünde render etmek detay üretmez."""
    import pymupdf

    from mixedmedia.core import scan_ingest

    pdf_path = tmp_path / "scan.pdf"
    _scan_like_pdf(pdf_path, dpi=400)

    with pymupdf.open(pdf_path) as doc:
        native = scan_ingest.pdf_native_dpi(doc[0])
    assert native == pytest.approx(400, rel=0.02)

    scans = list(scan_ingest.iter_scans([pdf_path], render_dpi=1200))
    assert scans[0].dpi_hint == pytest.approx(400, rel=0.02)


def test_a_vector_print_pdf_is_never_downgraded(tmp_path):
    """Bizim ürettiğimiz baskı sayfası kısıtlanmamalı.

    Marker'lar, kesim işaretleri ve metin vektördür; gömülü karelerin DPI'ına
    bakıp render'ı kısmak fiducial'ları okunamaz hâle getirirdi.
    """
    import pymupdf

    from mixedmedia.core import pdf_writer, scan_ingest

    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    with pymupdf.open(pdf_path) as doc:
        assert scan_ingest.pdf_native_dpi(doc[0]) is None

    scans = list(scan_ingest.iter_scans([pdf_path], render_dpi=300))
    assert scans[0].dpi_hint == 300.0


def test_a_low_resolution_scan_is_not_pushed_below_the_floor(tmp_path):
    """Çok düşük çözünürlüklü bir tarama fiducial eşiğinin altına indirilmez."""
    import pymupdf

    from mixedmedia.core import scan_ingest

    pdf_path = tmp_path / "kucuk.pdf"
    _scan_like_pdf(pdf_path, dpi=120)

    with pymupdf.open(pdf_path) as doc:
        assert scan_ingest.pdf_native_dpi(doc[0]) == pytest.approx(120, rel=0.05)

    scans = list(scan_ingest.iter_scans([pdf_path], render_dpi=400))
    assert scans[0].dpi_hint == 400.0


# ---------------------------------------------------------------------------
# Hücreler ham taramadan tek adımda çıkarılmalı
# ---------------------------------------------------------------------------


def test_cells_are_warped_straight_from_the_raw_scan(tmp_path):
    """Sayfa raster'ından kırpmak taramayı iki kez yeniden örnekler.

    Hücre, sayfa raster'ının çözünürlüğüyle sınırlı kalmamalı: tarayıcı ne
    verdiyse o kullanılabilmeli.
    """
    import cv2

    from mixedmedia.core import pdf_writer
    from mixedmedia.core.cell_extract import cell_image_rect, extract_cells
    from mixedmedia.core.rectify import rectify_scan

    from .test_roundtrip import render_and_scan

    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, work_dpi=200)
    assert page.source is not None and page.homography is not None

    # Sayfa raster'ı 200 DPI ama hücreyi 600 DPI isteyebilmeliyiz.
    cells = extract_cells(page, project.pages[0], project, cell_dpi=600)
    rect = cell_image_rect(project.pages[0].cells[0], project).expanded(-0.5, -0.5)
    expected_width = round(rect.w_mm / 25.4 * 600)

    assert abs(cells[0].image.shape[1] - expected_width) <= 2
    assert cells[0].image.shape[1] > page.image.shape[1] * 0.5


def test_cell_extraction_falls_back_when_the_homography_is_missing(tmp_path):
    """Manuel modda homografi olmayabilir; kırpma yolu çalışmayı sürdürmeli."""
    import cv2

    from mixedmedia.core import pdf_writer
    from mixedmedia.core.cell_extract import extract_cells
    from mixedmedia.core.rectify import rectify_scan

    from .test_roundtrip import render_and_scan

    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, work_dpi=300)
    page.source = None
    page.homography = None

    cells = extract_cells(page, project.pages[0], project)
    assert len(cells) == len(project.pages[0].cells)
    assert all(cell.image.size > 0 for cell in cells)


def test_marker_detection_survives_a_high_resolution_scan(tmp_path):
    """Yüksek çözünürlüklü taramada da dört köşe okunmalı.

    ArUco'nun uyarlamalı eşiği belirli bir marker/piksel oranı için ayarlıdır;
    tespit sabit bir ölçekte yapılmazsa yüksek DPI'da kağıt dokusu eşiklemeyi
    gürültülendirip marker'ları kaçırtır.
    """
    import cv2

    from mixedmedia.core import pdf_writer
    from mixedmedia.core.rectify import rectify_scan

    from .test_roundtrip import render_and_scan

    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", dpi=600, flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert bgr.shape[1] > 4000, "test taraması yüksek çözünürlüklü olmalı"

    page = rectify_scan(bgr, project, work_dpi=400)
    assert page.corners_found == 4
    assert page.confidence == "high"
    assert page.page_no == 1
