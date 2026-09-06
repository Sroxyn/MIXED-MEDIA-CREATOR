"""layout.py parametrik testleri (bkz. CLAUDE.md §10).

Doğrulanan garantiler:
  * Hiçbir hücre sayfa sınırını (kenar boşluğu dahil) aşmaz.
  * Hücreler arası boşluklar birbirine eşittir.
  * Toplam alan muhasebesi tutar.
  * A4/A3 sayfa boyutları punto cinsinden birebir doğrudur.
"""

from __future__ import annotations

import math

import pytest

from mixedmedia.core.constants import (
    CELL_MARKER_BAND_MM,
    FOOTER_HEIGHT_MM,
    MIN_EFFECTIVE_DPI,
)
from mixedmedia.core.errors import LayoutInvalid
from mixedmedia.core.layout import (
    build_pages,
    choose_best_grid,
    compute_grid,
    effective_dpi,
    factor_pairs,
    fit_image_box,
    grid_from_settings,
    mm_to_pt,
    page_count,
    paper_size_mm,
)
from mixedmedia.core.models import (
    Extraction,
    ImageFit,
    LayoutSettings,
    Orientation,
    PaperSize,
    Project,
)

TOL = 1e-9

PAPERS = [PaperSize.A4, PaperSize.A3]
ORIENTATIONS = [Orientation.PORTRAIT, Orientation.LANDSCAPE]
GRIDS = [(1, 1), (1, 2), (2, 1), (2, 3), (3, 2), (3, 3), (3, 4), (4, 3)]
BANDS = [0.0, CELL_MARKER_BAND_MM]


def _all_grids():
    for paper in PAPERS:
        for orient in ORIENTATIONS:
            for cols, rows in GRIDS:
                for band in BANDS:
                    yield paper, orient, cols, rows, band


# ---------------------------------------------------------------------------
# Sayfa boyutları
# ---------------------------------------------------------------------------


def test_a4_size_in_points_matches_spec():
    """A4 = 595.28 × 841.89 pt (CLAUDE.md §6.6)."""
    w_mm, h_mm = paper_size_mm(PaperSize.A4, Orientation.PORTRAIT)
    assert (w_mm, h_mm) == (210.0, 297.0)
    assert mm_to_pt(w_mm) == pytest.approx(595.2755905511812, abs=1e-9)
    assert mm_to_pt(h_mm) == pytest.approx(841.8897637795277, abs=1e-9)
    assert round(mm_to_pt(w_mm), 2) == 595.28
    assert round(mm_to_pt(h_mm), 2) == 841.89


def test_a3_size_in_points():
    w_mm, h_mm = paper_size_mm(PaperSize.A3, Orientation.PORTRAIT)
    assert (w_mm, h_mm) == (297.0, 420.0)
    assert round(mm_to_pt(w_mm), 2) == 841.89
    assert round(mm_to_pt(h_mm), 2) == 1190.55


def test_a3_is_exactly_two_a4_pages():
    a4_w, a4_h = paper_size_mm(PaperSize.A4, Orientation.PORTRAIT)
    a3_w, a3_h = paper_size_mm(PaperSize.A3, Orientation.PORTRAIT)
    assert a3_w == a4_h
    assert a3_h == pytest.approx(2 * a4_w, abs=TOL)


def test_landscape_swaps_dimensions():
    for paper in PAPERS:
        pw, ph = paper_size_mm(paper, Orientation.PORTRAIT)
        lw, lh = paper_size_mm(paper, Orientation.LANDSCAPE)
        assert (lw, lh) == (ph, pw)


# ---------------------------------------------------------------------------
# Izgara geometrisi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("paper,orient,cols,rows,band", list(_all_grids()))
def test_cells_stay_inside_page_margins(paper, orient, cols, rows, band):
    """Hiçbir hücre (ve marker şeridi) kenar boşluğunu veya footer'ı ihlal etmez."""
    geom = compute_grid(paper, orient, cols, rows, 12.0, 14.0, marker_band_mm=band)
    content = geom.content_rect

    for i in range(geom.cells_per_page):
        cut = geom.cut_rect(i)
        assert cut.x_mm >= content.x_mm - TOL
        assert cut.y_mm >= content.y_mm - TOL
        assert cut.right_mm <= content.right_mm + TOL
        assert cut.bottom_mm <= content.bottom_mm + TOL


@pytest.mark.parametrize("paper,orient,cols,rows,band", list(_all_grids()))
def test_gutters_are_equal(paper, orient, cols, rows, band):
    """Yatay ve dikey boşluklar her yerde tam olarak gutter kadar."""
    gutter = 14.0
    geom = compute_grid(paper, orient, cols, rows, 12.0, gutter, marker_band_mm=band)

    for row in range(rows):
        for col in range(cols - 1):
            left = geom.cell_at(row * cols + col)
            right = geom.cell_at(row * cols + col + 1)
            assert right.x_mm - left.right_mm == pytest.approx(gutter, abs=1e-9)

    for row in range(rows - 1):
        for col in range(cols):
            upper = geom.cut_rect(row * cols + col)
            lower = geom.cell_at((row + 1) * cols + col)
            assert lower.y_mm - upper.bottom_mm == pytest.approx(gutter, abs=1e-9)


@pytest.mark.parametrize("paper,orient,cols,rows,band", list(_all_grids()))
def test_grid_fills_content_area_exactly(paper, orient, cols, rows, band):
    """Alan muhasebesi: hücreler + şeritler + boşluklar = kullanılabilir alan."""
    gutter = 14.0
    geom = compute_grid(paper, orient, cols, rows, 12.0, gutter, marker_band_mm=band)
    content = geom.content_rect

    used_w = cols * geom.cell_w_mm + (cols - 1) * gutter
    used_h = rows * (geom.cell_h_mm + band) + (rows - 1) * gutter
    assert used_w == pytest.approx(content.w_mm, abs=1e-9)
    assert used_h == pytest.approx(content.h_mm, abs=1e-9)

    first, last = geom.cell_at(0), geom.cut_rect(geom.cells_per_page - 1)
    assert first.x_mm == pytest.approx(content.x_mm, abs=TOL)
    assert first.y_mm == pytest.approx(content.y_mm, abs=TOL)
    assert last.right_mm == pytest.approx(content.right_mm, abs=1e-9)
    assert last.bottom_mm == pytest.approx(content.bottom_mm, abs=1e-9)


@pytest.mark.parametrize("paper,orient,cols,rows,band", list(_all_grids()))
def test_all_cells_have_identical_size(paper, orient, cols, rows, band):
    geom = compute_grid(paper, orient, cols, rows, 12.0, 14.0, marker_band_mm=band)
    for cell in geom.cells:
        assert cell.w_mm == pytest.approx(geom.cell_w_mm, abs=TOL)
        assert cell.h_mm == pytest.approx(geom.cell_h_mm, abs=TOL)
    assert len(geom.cells) == cols * rows


def test_cells_do_not_overlap():
    geom = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 3, 4, 10.0, 8.0, marker_band_mm=10.0)
    rects = [geom.cut_rect(i) for i in range(geom.cells_per_page)]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            overlap_x = min(a.right_mm, b.right_mm) - max(a.x_mm, b.x_mm)
            overlap_y = min(a.bottom_mm, b.bottom_mm) - max(a.y_mm, b.y_mm)
            assert overlap_x <= TOL or overlap_y <= TOL


def test_spec_formula_holds_when_cell_markers_disabled():
    """Marker şeridi yokken hesap CLAUDE.md §6.4 formülüyle birebir aynı."""
    paper_w, paper_h = paper_size_mm(PaperSize.A4, Orientation.PORTRAIT)
    margin, gutter, cols, rows = 12.0, 14.0, 2, 3
    geom = compute_grid(PaperSize.A4, Orientation.PORTRAIT, cols, rows, margin, gutter)

    usable_w = paper_w - 2 * margin
    usable_h = paper_h - 2 * margin - FOOTER_HEIGHT_MM
    assert geom.cell_w_mm == pytest.approx((usable_w - (cols - 1) * gutter) / cols, abs=TOL)
    assert geom.cell_h_mm == pytest.approx((usable_h - (rows - 1) * gutter) / rows, abs=TOL)


def test_marker_band_only_shrinks_height():
    plain = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 2, 3, 12.0, 14.0)
    banded = compute_grid(
        PaperSize.A4, Orientation.PORTRAIT, 2, 3, 12.0, 14.0, marker_band_mm=CELL_MARKER_BAND_MM
    )
    assert banded.cell_w_mm == pytest.approx(plain.cell_w_mm, abs=TOL)
    assert banded.cell_h_mm == pytest.approx(plain.cell_h_mm - CELL_MARKER_BAND_MM, abs=1e-9)


# ---------------------------------------------------------------------------
# Geçersiz parametreler
# ---------------------------------------------------------------------------


def test_impossible_grid_raises_user_facing_error():
    with pytest.raises(LayoutInvalid) as exc:
        compute_grid(PaperSize.A4, Orientation.PORTRAIT, 40, 40, 12.0, 14.0)
    assert "sığmıyor" in str(exc.value)


def test_negative_margin_rejected():
    with pytest.raises(LayoutInvalid):
        compute_grid(PaperSize.A4, Orientation.PORTRAIT, 2, 2, -1.0, 4.0)


def test_zero_cols_rejected():
    with pytest.raises(LayoutInvalid):
        compute_grid(PaperSize.A4, Orientation.PORTRAIT, 0, 2, 10.0, 4.0)


# ---------------------------------------------------------------------------
# Görüntü kutusu
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ar", [16 / 9, 9 / 16, 4 / 3, 1.0, 2.39])
def test_contain_box_fits_inside_cell_and_keeps_aspect(ar):
    geom = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 2, 3, 12.0, 14.0)
    cell = geom.cell_at(0)
    box = fit_image_box(cell, ar, ImageFit.CONTAIN)

    assert box.w_mm <= cell.w_mm + TOL
    assert box.h_mm <= cell.h_mm + TOL
    assert box.w_mm / box.h_mm == pytest.approx(ar, rel=1e-12)
    # En az bir kenar hücreye tam oturur.
    assert box.w_mm == pytest.approx(cell.w_mm, abs=1e-9) or box.h_mm == pytest.approx(
        cell.h_mm, abs=1e-9
    )
    # Ortalanmış.
    assert box.x_mm - cell.x_mm == pytest.approx(cell.right_mm - box.right_mm, abs=1e-9)
    assert box.y_mm - cell.y_mm == pytest.approx(cell.bottom_mm - box.bottom_mm, abs=1e-9)


@pytest.mark.parametrize("ar", [16 / 9, 9 / 16, 1.0])
def test_cover_box_covers_cell(ar):
    geom = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 2, 3, 12.0, 14.0)
    cell = geom.cell_at(0)
    box = fit_image_box(cell, ar, ImageFit.COVER)
    assert box.w_mm >= cell.w_mm - TOL
    assert box.h_mm >= cell.h_mm - TOL
    assert box.w_mm / box.h_mm == pytest.approx(ar, rel=1e-12)


def test_zero_aspect_ratio_rejected():
    cell = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 1, 1, 10.0, 0.0).cell_at(0)
    with pytest.raises(LayoutInvalid):
        fit_image_box(cell, 0.0)


# ---------------------------------------------------------------------------
# Otomatik ızgara seçimi
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 4, 6, 9, 12])
def test_factor_pairs_are_complete_and_valid(n):
    pairs = list(factor_pairs(n))
    assert all(c * r == n for c, r in pairs)
    assert len(pairs) == len({p for p in pairs})
    assert (1, n) in pairs and (n, 1) in pairs


@pytest.mark.parametrize("n", [1, 2, 4, 6, 9, 12])
@pytest.mark.parametrize("ar", [16 / 9, 9 / 16, 4 / 3])
def test_auto_grid_maximizes_printed_area(n, ar):
    """Seçilen aday, tüm geçerli adaylar arasında en büyük toplam alana sahip."""
    best = choose_best_grid(n, ar, PaperSize.A4, 12.0, 14.0)
    assert best.cols * best.rows == n

    areas = []
    for orient in ORIENTATIONS:
        for cols, rows in factor_pairs(n):
            try:
                geom = compute_grid(PaperSize.A4, orient, cols, rows, 12.0, 14.0)
            except LayoutInvalid:
                continue
            areas.append(fit_image_box(geom.cells[0], ar).area_mm2 * n)
    assert best.total_image_area_mm2 == pytest.approx(max(areas), rel=1e-12)


def test_auto_grid_prefers_landscape_for_wide_video_at_one_per_page():
    best = choose_best_grid(1, 16 / 9, PaperSize.A4, 12.0, 14.0)
    assert best.orientation is Orientation.LANDSCAPE


def test_auto_grid_prefers_portrait_for_tall_video_at_one_per_page():
    best = choose_best_grid(1, 9 / 16, PaperSize.A4, 12.0, 14.0)
    assert best.orientation is Orientation.PORTRAIT


def test_auto_grid_raises_when_nothing_fits():
    with pytest.raises(LayoutInvalid):
        choose_best_grid(12, 16 / 9, PaperSize.A4, 90.0, 40.0)


# ---------------------------------------------------------------------------
# Türetilmiş bilgiler
# ---------------------------------------------------------------------------


def test_effective_dpi_matches_manual_calculation():
    assert effective_dpi(1920, 25.4) == pytest.approx(1920.0, abs=TOL)
    assert effective_dpi(1920, 254.0) == pytest.approx(192.0, abs=1e-9)


def test_effective_dpi_warning_threshold_is_meaningful():
    """A4'te 2×3 ızgarada 1920px kaynak rahatlıkla eşiğin üstünde kalmalı."""
    geom = compute_grid(PaperSize.A4, Orientation.PORTRAIT, 2, 3, 12.0, 14.0)
    box = fit_image_box(geom.cell_at(0), 16 / 9)
    assert effective_dpi(1920, box.w_mm) > MIN_EFFECTIVE_DPI


@pytest.mark.parametrize(
    "frames,per_page,expected",
    [(0, 6, 0), (1, 6, 1), (6, 6, 1), (7, 6, 2), (75, 6, 13), (144, 6, 24)],
)
def test_page_count(frames, per_page, expected):
    assert page_count(frames, per_page) == expected


# ---------------------------------------------------------------------------
# Sayfa oluşturma
# ---------------------------------------------------------------------------


def _project(frame_count: int = 75, **layout_kw) -> Project:
    kw = {"paper": PaperSize.A4, "cols": 2, "rows": 3}
    kw.update(layout_kw)
    return Project(
        name="test",
        project_id="mm_7f3a9c",
        extraction=Extraction(target_fps=6, frame_count=frame_count),
        layout=LayoutSettings(**kw),
    )


def test_build_pages_assigns_every_frame_exactly_once():
    project = _project(75)
    pages = build_pages(project)

    assert len(pages) == page_count(75, 6)
    indices = [c.frame_index for p in pages for c in p.cells]
    assert indices == list(range(75))


def test_build_pages_last_page_is_partial():
    pages = build_pages(_project(75))
    assert len(pages[-1].cells) == 75 - 6 * (len(pages) - 1)
    assert all(len(p.cells) == 6 for p in pages[:-1])


def test_build_pages_cell_ids_are_unique_and_stable():
    pages = build_pages(_project(75))
    ids = [c.cell_id for p in pages for c in p.cells]
    assert len(ids) == len(set(ids))
    assert ids[0] == "mm_7f3a9c-p001-c00"
    assert pages[0].cells[5].cell_id == "mm_7f3a9c-p001-c05"
    assert pages[1].cells[0].cell_id == "mm_7f3a9c-p002-c00"


def test_build_pages_coordinates_match_grid():
    project = _project(12)
    geom = grid_from_settings(project.layout)
    for page in build_pages(project):
        for slot, cell in enumerate(page.cells):
            rect = geom.cell_at(slot)
            assert cell.x_mm == pytest.approx(rect.x_mm, abs=1e-4)
            assert cell.y_mm == pytest.approx(rect.y_mm, abs=1e-4)
            assert cell.w_mm == pytest.approx(rect.w_mm, abs=1e-4)
            assert cell.h_mm == pytest.approx(rect.h_mm, abs=1e-4)
            # Sözlük yetiyorsa marker kare indeksini taşır; kesilen kart
            # böylece kendini tam olarak tanıtır (bkz. uses_global_marker_ids).
            assert cell.marker_id == cell.frame_index


def test_marker_ids_carry_the_frame_index_when_they_fit():
    """Kesilen kart, üzerindeki marker'dan hangi kare olduğunu söyleyebilmeli."""
    from mixedmedia.core.layout import uses_global_marker_ids

    project = _project(12)
    assert uses_global_marker_ids(12)
    ids = [cell.marker_id for page in build_pages(project) for cell in page.cells]
    assert ids == list(range(12))


def test_marker_ids_fall_back_to_slots_on_long_projects():
    """Sözlük yetmezse sayfa içi sıraya düşülür; kart o zaman sayfasını bilmez."""
    from mixedmedia.core.layout import uses_global_marker_ids
    from mixedmedia.core.markers import cell_marker_capacity

    frame_count = cell_marker_capacity() + 1
    assert not uses_global_marker_ids(frame_count)
    project = _project(frame_count)
    pages = build_pages(project)
    assert [cell.marker_id for cell in pages[0].cells] == list(range(6))
    assert [cell.marker_id for cell in pages[1].cells] == list(range(6))


def test_build_pages_frame_files_follow_naming():
    pages = build_pages(_project(7))
    assert pages[0].cells[0].frame_file == "frames/frame_00001.png"
    assert pages[1].cells[0].frame_file == "frames/frame_00007.png"


def test_build_pages_empty_project_yields_no_pages():
    assert build_pages(_project(0)) == []


def test_spec_example_75_frames_at_6_per_page_needs_13_pages():
    """CLAUDE.md §5 örneği: 12.48 sn @ 6 fps = 75 kare."""
    assert math.ceil(12.48 * 6) == 75
    assert page_count(75, 6) == 13
