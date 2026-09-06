"""Sentetik round-trip testi — projenin en kritik testi (bkz. CLAUDE.md §10).

Akış:

1. Her karesinde büyük, okunabilir bir numara olan sentetik kareler üretilir.
2. Import boru hattı çalıştırılır, PDF üretilir.
3. PDF sayfaları raster'a çevrilip **tarama simüle edilir**: ±3° dönme,
   %2 perspektif bozulması, gauss gürültüsü, sarı renk kayması, %3 ölçek farkı
   ve bir sayfa 180° ters.
4. Export boru hattı çalıştırılır.
5. Her çıkan karenin şablon eşleşmesiyle okunan numarası, beklenen
   ``frame_index`` ile birebir aynı olmalı. Doğruluk **%100** olmalıdır.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from mixedmedia.core import pdf_writer, pipeline, sequencer
from mixedmedia.core.errors import MarkerDetectionFailed
from mixedmedia.core.models import MissingFramePolicy
from mixedmedia.core.rectify import rectify_scan

from .conftest import build_project, make_frame

PRINT_DPI = 300
SCAN_SEED = 20260905

# Şablon eşleşmesinin yapıldığı ortak boyut: rakamları ayırt etmeye yeter,
# testi yavaşlatmaz.
MATCH_SIZE = (96, 54)


# ---------------------------------------------------------------------------
# Tarama simülasyonu
# ---------------------------------------------------------------------------


def simulate_scan(
    page: np.ndarray,
    rng: np.random.Generator,
    *,
    angle_deg: float = 0.0,
    perspective: float = 0.0,
    scale: float = 1.0,
    rotate_180: bool = False,
    yellow_cast: float = 0.0,
    noise_sigma: float = 0.0,
) -> np.ndarray:
    """Basılı sayfayı tarayıcıdan geçmiş gibi bozar."""
    image = page
    if scale != 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    if rotate_180:
        image = cv2.rotate(image, cv2.ROTATE_180)

    # Dönme sırasında köşe işaretleri kırpılmasın diye beyaz kenar eklenir.
    pad = int(0.06 * max(image.shape[:2]))
    image = cv2.copyMakeBorder(
        image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    height, width = image.shape[:2]

    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    jitter = perspective * min(width, height)
    warped_corners = corners + rng.uniform(-jitter, jitter, corners.shape).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(corners, warped_corners)

    rotation = cv2.getRotationMatrix2D((width / 2, height / 2), angle_deg, 1.0)
    matrix = np.vstack([rotation, [0, 0, 1]]) @ matrix

    image = cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderValue=(255, 255, 255),
    )

    result = image.astype(np.float32)
    if yellow_cast:
        # BGR'de mavi kanalı kısmak sarıya kaydırır.
        result[:, :, 0] *= 1.0 - yellow_cast
    if noise_sigma:
        result += rng.normal(0.0, noise_sigma, result.shape)
    return np.clip(result, 0, 255).astype(np.uint8)


def render_and_scan(
    pdf_path: Path,
    out_dir: Path,
    *,
    dpi: int = PRINT_DPI,
    flip_page: int | None = 2,
    skip_pages: set[int] | None = None,
) -> list[Path]:
    """PDF'i basıp tarar; yazılan tarama dosyalarının yollarını verir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SCAN_SEED)
    skip = skip_pages or set()
    written: list[Path] = []

    with pymupdf.open(pdf_path) as doc:
        for index in range(doc.page_count):
            page_no = index + 1
            if page_no in skip:
                continue
            pix = doc[index].get_pixmap(dpi=dpi)
            array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            bgr = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)

            scanned = simulate_scan(
                bgr,
                rng,
                angle_deg=float(rng.uniform(-3.0, 3.0)),
                perspective=0.02,
                scale=0.97,
                rotate_180=(page_no == flip_page),
                yellow_cast=0.10,
                noise_sigma=4.0,
            )
            target = out_dir / f"scan_{page_no:03d}.png"
            cv2.imencode(".png", scanned)[1].tofile(target)
            written.append(target)

    return written


# ---------------------------------------------------------------------------
# Şablon eşleşmesi ile doğrulama
# ---------------------------------------------------------------------------


def _canonical(image: np.ndarray) -> np.ndarray:
    """Karşılaştırma için ortak boyut + sıfır ortalama, birim varyans.

    Baskı yoğunluğu, gri tona çevirme ve tarama renk kayması parlaklık ve
    kontrastı değiştirir; normalizasyon bunları etkisiz kılar, geriye yalnızca
    şeklin kendisi kalır.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    resized = cv2.resize(gray, MATCH_SIZE, interpolation=cv2.INTER_AREA).astype(np.float32)
    resized -= resized.mean()
    norm = float(np.linalg.norm(resized))
    return resized / norm if norm > 1e-6 else resized


def identify_frame(cell_image: np.ndarray, templates: list[np.ndarray]) -> tuple[int, float]:
    """Kesilen kareyi şablonlarla eşleştirir; ``(indeks, benzerlik)`` döner."""
    probe = _canonical(cell_image)
    scores = [float((probe * template).sum()) for template in templates]
    best = int(np.argmax(scores))
    return best, scores[best]


def build_templates(frame_count: int) -> list[np.ndarray]:
    """Beklenen kareler için normalize edilmiş şablonlar."""
    templates = []
    for index in range(frame_count):
        frame = make_frame(index)
        templates.append(_canonical(cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR)))
        frame.close()
    return templates


# ---------------------------------------------------------------------------
# Ana kabul testi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cols,rows", [(2, 2), (3, 3)])
def test_synthetic_round_trip_is_exact(tmp_path, cols, rows):
    """Her çıkan karenin numarası beklenen frame_index ile birebir aynı olmalı."""
    frame_count = cols * rows * 3  # 3 sayfa
    workspace, project = build_project(
        tmp_path / "proj", frame_count=frame_count, cols=cols, rows=rows
    )

    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=2)
    assert len(scans) == len(project.pages)

    state = pipeline.run_ingest(workspace, project, scans)

    # Her sayfa okundu ve doğru numarayla eşleşti.
    assert sorted(page.page_no for page in state.pages) == [
        page.page_no for page in project.pages
    ]
    assert all(page.method != "failed" for page in state.pages)

    # Her kare bulundu.
    assert state.found_indices == set(range(frame_count)), (
        f"Eksik kareler: {sorted(set(range(frame_count)) - state.found_indices)}"
    )

    # Ve her kare DOĞRU kare — kabul koşulu bu.
    templates = build_templates(frame_count)
    mismatches: list[tuple[int, int, float]] = []
    for cell in state.cells:
        image = cv2.imdecode(
            np.fromfile(workspace.resolve(cell.file), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        identified, score = identify_frame(image, templates)
        if identified != cell.frame_index:
            mismatches.append((cell.frame_index, identified, score))

    assert not mismatches, f"Yanlış eşleşen kareler (beklenen, bulunan, skor): {mismatches}"


def test_flipped_page_is_recovered_upright(tmp_path):
    """180° ters taranan sayfa kimlikli marker'lar sayesinde kendiliğinden düzelir."""
    workspace, project = build_project(tmp_path / "proj", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=1)

    state = pipeline.run_ingest(workspace, project, scans)

    flipped = next(page for page in state.pages if page.page_no == 1)
    assert flipped.rotated_180
    assert any("ters" in warning for warning in flipped.warnings)

    templates = build_templates(8)
    for cell in state.cells:
        if cell.page_no != 1:
            continue
        image = cv2.imdecode(
            np.fromfile(workspace.resolve(cell.file), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        assert identify_frame(image, templates)[0] == cell.frame_index


def test_scans_in_shuffled_order_still_map_to_right_pages(tmp_path):
    """Sayfa QR'ı sayfayı tanıtır; tarama sırası önemsizdir (§7.3)."""
    workspace, project = build_project(tmp_path / "proj", frame_count=12, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    shuffled = [scans[2], scans[0], scans[1]]
    state = pipeline.run_ingest(workspace, project, shuffled)

    by_source = {page.source: page.page_no for page in state.pages}
    assert by_source["scan_001.png"] == 1
    assert by_source["scan_002.png"] == 2
    assert by_source["scan_003.png"] == 3
    assert state.found_indices == set(range(12))


def test_rectification_produces_exact_paper_size(tmp_path):
    """Düzeltilmiş sayfa tam kağıt ölçüsünde olmalı: 1 mm = sabit piksel."""
    workspace, project = build_project(tmp_path / "proj", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, source_name=scans[0].name, work_dpi=300)

    assert page.image.shape[1] == round(210 / 25.4 * 300)
    assert page.image.shape[0] == round(297 / 25.4 * 300)
    assert page.corners_found == 4
    assert page.confidence == "high"
    assert page.page_no == 1


# ---------------------------------------------------------------------------
# Eksik sayfa senaryosu (§10)
# ---------------------------------------------------------------------------


def test_missing_pages_are_filled_by_hold_policy(tmp_path):
    """10 sayfadan 2'si taranmamışken 'hold' politikası doğru çalışmalı."""
    workspace, project = build_project(tmp_path / "proj", frame_count=20, cols=1, rows=2)
    assert len(project.pages) == 10

    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    render_and_scan(pdf_path, tmp_path / "scans", flip_page=None, skip_pages={4, 7})

    state = pipeline.run_ingest(workspace, project, [tmp_path / "scans"])
    assert len(state.pages) == 8

    missing_frames = {6, 7, 12, 13}  # 4. ve 7. sayfadaki kareler
    assert state.found_indices == set(range(20)) - missing_frames

    available = {
        cell.frame_index: workspace.resolve(cell.file) for cell in state.cells
    }
    slots, report = sequencer.build_sequence(available, 20, MissingFramePolicy.HOLD)

    assert sorted(report.missing) == sorted(missing_frames)
    assert report.output_length == 20, "hold politikasında dizi kısalmamalı"
    # Eksik kareler bir önceki bulunan kareyi tekrarlar.
    assert slots[6].path == available[5]
    assert slots[7].path == available[5]
    assert slots[12].path == available[11]
    assert all(slot.resolution in ("found", "hold") for slot in slots)


def test_skip_policy_shortens_sequence(tmp_path):
    available = {0: Path("a.png"), 2: Path("c.png")}
    slots, report = sequencer.build_sequence(available, 3, MissingFramePolicy.SKIP)
    assert [slot.frame_index for slot in slots] == [0, 2]
    assert report.missing == [1]


def test_placeholder_policy_marks_gaps():
    available = {0: Path("a.png")}
    slots, _ = sequencer.build_sequence(available, 3, MissingFramePolicy.PLACEHOLDER)
    assert [slot.resolution for slot in slots] == ["found", "placeholder", "placeholder"]


def test_hold_policy_pulls_forward_when_first_frames_missing():
    available = {2: Path("c.png")}
    slots, _ = sequencer.build_sequence(available, 3, MissingFramePolicy.HOLD)
    assert all(slot.path == available[2] for slot in slots)


# ---------------------------------------------------------------------------
# Marker'sız mod
# ---------------------------------------------------------------------------


def test_scan_without_markers_reports_a_usable_error(tmp_path):
    """Marker kapalıyken tespit başarısız olmalı ve mesaj yol göstermeli (§7.4)."""
    workspace, project = build_project(
        tmp_path / "proj",
        frame_count=4,
        cols=2,
        rows=2,
        markers_enabled=False,
        calibration_strip=False,
    )
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    with pytest.raises(MarkerDetectionFailed) as exc:
        rectify_scan(bgr, project, source_name=scans[0].name)
    assert "manuel hizalama" in str(exc.value)


def test_ingest_skips_unreadable_pages_without_aborting(tmp_path):
    """Bir sayfa okunamazsa iş durmamalı; diğer sayfalar alınmalı."""
    workspace, project = build_project(tmp_path / "proj", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    # İlk taramayı boş bir sayfaya çevir: hiçbir işaret okunamaz.
    blank = np.full((1200, 850, 3), 255, dtype=np.uint8)
    cv2.imencode(".png", blank)[1].tofile(scans[0])

    state = pipeline.run_ingest(workspace, project, scans)
    failed = [page for page in state.pages if page.method == "failed"]
    assert len(failed) == 1
    assert state.found_indices == {4, 5, 6, 7}
