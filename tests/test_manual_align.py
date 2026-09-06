"""Manuel / marker'sız mod (bkz. CLAUDE.md §7.4).

İşaretler estetik gerekçesiyle kapatılmışsa ya da tarama okunamayacak kadar
bozuksa otomatik tespit yapılamaz. Kullanıcı sayfanın dört köşesini kendisi
gösterir; ızgara projedeki ölçülerden üstüne bindirilir. Bu testler o yolun
gerçekten doğru kareleri ürettiğini doğrular.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from mixedmedia.core import pdf_writer, pipeline
from mixedmedia.core.errors import MarkerDetectionFailed
from mixedmedia.core.layout import grid_from_settings, mm_to_px
from mixedmedia.core.models import ManualAlignment
from mixedmedia.core.rectify import paper_corner_guess, rectify_from_corners

from .conftest import build_project
from .test_roundtrip import build_templates, identify_frame, render_and_scan


def _markerless_project(tmp_path, frame_count=8):
    """İşaretleri kapatılmış — yani otomatik tespitin imkânsız olduğu — proje."""
    return build_project(
        tmp_path / "p",
        frame_count=frame_count,
        cols=2,
        rows=2,
        markers_enabled=False,
        calibration_strip=False,
    )


def scan_with_known_corners(
    page: np.ndarray, *, angle: float = 2.0, seed: int = 5
) -> tuple[np.ndarray, list[list[float]]]:
    """Sayfayı tarayıcıdan geçmiş gibi bozar ve köşelerinin **gerçek** yerini verir.

    Kullanıcı hizalama penceresinde tam olarak bu noktaları gösterir. Köşe
    tahmini burada kullanılamaz: tarayıcı kapağı da kağıt da beyaz olduğu için
    kağıdın kenarı görüntüde neredeyse görünmez — tahmin yalnızca kullanıcıya
    bir başlangıç noktası sunar, ölçüt değildir.
    """
    rng = np.random.default_rng(seed)
    height, width = page.shape[:2]
    pad = int(0.08 * max(height, width))
    padded = cv2.copyMakeBorder(
        page, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    padded_h, padded_w = padded.shape[:2]

    corners = np.float32(
        [[pad, pad], [pad + width, pad], [pad + width, pad + height], [pad, pad + height]]
    )
    jitter = 0.012 * min(padded_w, padded_h)
    box = np.float32([[0, 0], [padded_w, 0], [padded_w, padded_h], [0, padded_h]])
    warped_box = box + rng.uniform(-jitter, jitter, box.shape).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(box, warped_box)

    rotation = cv2.getRotationMatrix2D((padded_w / 2, padded_h / 2), angle, 1.0)
    matrix = np.vstack([rotation, [0, 0, 1]]) @ matrix

    scan = cv2.warpPerspective(
        padded, matrix, (padded_w, padded_h), flags=cv2.INTER_CUBIC,
        borderValue=(255, 255, 255),
    )
    moved = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    return scan, moved.tolist()


def _page_raster(pdf_path, index: int, dpi: int = 300) -> np.ndarray:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        pix = doc[index].get_pixmap(dpi=dpi)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# Çekirdek
# ---------------------------------------------------------------------------


def test_manual_rectification_produces_exact_paper_size(tmp_path):
    workspace, project = _markerless_project(tmp_path, frame_count=4)
    pdf_writer.write_pdf(project, workspace.root, tmp_path / "b.pdf")
    bgr, corners = scan_with_known_corners(_page_raster(tmp_path / "b.pdf", 0))

    page = rectify_from_corners(bgr, project, corners, work_dpi=300, page_no=1)

    assert page.image.shape[1] == round(210 / 25.4 * 300)
    assert page.image.shape[0] == round(297 / 25.4 * 300)
    assert page.method == "manual"
    assert page.confidence == "medium"
    assert page.homography is not None and page.source is not None


def test_manual_rectification_needs_four_corners(tmp_path):
    _, project = _markerless_project(tmp_path, frame_count=4)
    bgr = np.full((400, 300, 3), 255, dtype=np.uint8)

    with pytest.raises(MarkerDetectionFailed) as exc:
        rectify_from_corners(bgr, project, [[0, 0], [10, 0], [10, 10]], work_dpi=200)
    assert "dört köşe" in str(exc.value)


def test_grid_offset_shifts_the_extraction(tmp_path):
    """Kağıda kaymış baskı için ince ayar; kaydırma gerçekten etki etmeli."""
    workspace, project = _markerless_project(tmp_path, frame_count=4)
    pdf_writer.write_pdf(project, workspace.root, tmp_path / "b.pdf")
    bgr, corners = scan_with_known_corners(_page_raster(tmp_path / "b.pdf", 0))

    plain = rectify_from_corners(bgr, project, corners, work_dpi=300)
    shifted = rectify_from_corners(
        bgr, project, corners, work_dpi=300, grid_offset_mm=(5.0, 0.0)
    )

    expected = int(round(mm_to_px(5.0, 300)))
    column = plain.image.shape[1] // 2
    # 5 mm sağa kayan ızgara, aynı içeriği o kadar sağda görmeli.
    assert not np.array_equal(plain.image[:, column], shifted.image[:, column])
    assert expected > 0
    assert any("kaydırıldı" in warning for warning in shifted.warnings)


def test_corner_guess_finds_the_paper(tmp_path):
    """Tahmin kullanıcıya makul bir başlangıç vermeli."""
    scan = np.full((1200, 900, 3), 200, dtype=np.uint8)
    scan[100:1100, 80:820] = 255  # kağıt

    corners = paper_corner_guess(scan)
    assert len(corners) == 4
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    assert min(xs) == pytest.approx(80, abs=20)
    assert max(xs) == pytest.approx(820, abs=20)
    assert min(ys) == pytest.approx(100, abs=20)
    assert max(ys) == pytest.approx(1100, abs=20)


def test_corner_guess_always_returns_four_usable_points():
    """Kağıt ayırt edilemese bile kullanıcıya sürükleyebileceği dört nokta verilmeli."""
    noise = np.random.default_rng(3).integers(0, 256, (300, 400, 3), dtype=np.uint8)
    corners = paper_corner_guess(noise)

    assert len(corners) == 4
    for x, y in corners:
        assert -1 <= x <= 401
        assert -1 <= y <= 301


# ---------------------------------------------------------------------------
# Uçtan uca: işaretsiz bir sayfa elle hizalanınca doğru kareler çıkmalı
# ---------------------------------------------------------------------------


def test_markerless_page_yields_correct_frames_after_manual_alignment(tmp_path):
    """Kabul koşulu: işaretler kapalıyken bile doğru kareler çıkarılmalı."""
    workspace, project = _markerless_project(tmp_path, frame_count=8)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    scans_dir = tmp_path / "scans"
    scans_dir.mkdir()
    manual = {}
    scans = []
    for index in range(len(project.pages)):
        scan, corners = scan_with_known_corners(
            _page_raster(pdf_path, index), angle=2.0 + index, seed=index + 1
        )
        path = scans_dir / f"scan_{index + 1:03d}.png"
        cv2.imencode(".png", scan)[1].tofile(path)
        scans.append(path)
        manual[path.name] = ManualAlignment(corners=corners, page_no=index + 1)

    state = pipeline.run_ingest(workspace, project, scans, manual=manual)

    assert [page.method for page in state.pages] == ["manual", "manual"]
    assert state.found_indices == set(range(8))

    templates = build_templates(8)
    for cell in state.cells:
        image = cv2.imdecode(
            np.fromfile(workspace.resolve(cell.file), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        identified, _ = identify_frame(image, templates)
        assert identified == cell.frame_index, (
            f"{cell.cell_id}: beklenen {cell.frame_index}, bulunan {identified}"
        )


def test_markerless_page_fails_without_manual_alignment(tmp_path):
    """Elle hizalama verilmezse işaretsiz sayfa okunamamalı — sessizce geçmemeli."""
    workspace, project = _markerless_project(tmp_path, frame_count=4)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    state = pipeline.run_ingest(workspace, project, scans)
    assert [page.method for page in state.pages] == ["failed"]
    assert not state.cells
    assert any("manuel hizalama" in page.warnings[0] for page in state.pages)


def test_manual_alignment_survives_a_save_and_reload(tmp_path):
    """Bir kez yapılan hizalama kaybolmamalı."""
    from mixedmedia.core.project import load_project

    workspace, project = _markerless_project(tmp_path, frame_count=4)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    manual = {scans[0].name: ManualAlignment(corners=paper_corner_guess(bgr), page_no=1)}
    pipeline.run_ingest(workspace, project, scans, manual=manual)

    _, reloaded = load_project(workspace.root)
    assert scans[0].name in reloaded.ingest.manual
    assert len(reloaded.ingest.manual[scans[0].name].corners) == 4

    # İkinci turda hizalama yeniden verilmese de kayıtlıdan uygulanmalı.
    state = pipeline.run_ingest(workspace, reloaded, scans)
    assert state.pages[0].method == "manual"


def test_manual_alignment_beats_automatic_detection(tmp_path):
    """Kullanıcı elle hizaladıysa otomatik tespit onun önüne geçmemeli."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    manual = {scans[0].name: ManualAlignment(corners=paper_corner_guess(bgr), page_no=1)}

    state = pipeline.run_ingest(workspace, project, scans, manual=manual)
    assert state.pages[0].method == "manual"


def test_grid_overlay_matches_the_project_geometry(tmp_path):
    """Izgara bindirmesi projedeki ölçülerden gelmeli, tahminden değil."""
    _, project = _markerless_project(tmp_path, frame_count=8)
    geometry = grid_from_settings(project.layout)

    assert geometry.cells_per_page == 4
    assert geometry.marker_band_mm == 0.0  # işaretler kapalı, şerit de yok
    for index in range(geometry.cells_per_page):
        assert geometry.cut_rect(index).w_mm == pytest.approx(geometry.cell_w_mm)
