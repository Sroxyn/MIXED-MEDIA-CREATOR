"""Kesilmiş kart modu (bkz. CLAUDE.md §7.3).

Sanatçı kareleri makasla kestiğinde sayfa fiducial'ları kalmaz; geriye yalnızca
her karenin altındaki mikro-marker kalır — kesim çizgisi zaten onu kartın
içinde bırakacak şekilde çizilir. Bu testler kartların karışık, döndürülmüş ve
sayfasından kopuk hâlde bile doğru kareye eşlendiğini doğrular.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest
import pymupdf

from mixedmedia.core import pdf_writer, pipeline
from mixedmedia.core.cell_extract import extract_loose_cards
from mixedmedia.core.errors import MarkerDetectionFailed
from mixedmedia.core.layout import (
    grid_from_settings,
    mm_to_px,
    uses_global_marker_ids,
)
from mixedmedia.core.markers import cell_marker_capacity

from .conftest import build_project
from .test_roundtrip import build_templates, identify_frame

CARD_PAD_PX = 90
PRINT_DPI = 300


def _page_raster(pdf_path, page_index: int, dpi: int = PRINT_DPI) -> np.ndarray:
    with pymupdf.open(pdf_path) as doc:
        pix = doc[page_index].get_pixmap(dpi=dpi)
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)


def cut_cards(page: np.ndarray, project, *, angles, dpi: int = PRINT_DPI):
    """Sayfayı kesim çizgilerinden makasla keser ve kartları döndürür."""
    geometry = grid_from_settings(project.layout)
    cards = []
    for slot, angle in enumerate(angles):
        cut = geometry.cut_rect(slot)
        box = [
            int(mm_to_px(value, dpi))
            for value in (cut.x_mm, cut.y_mm, cut.right_mm, cut.bottom_mm)
        ]
        card = page[box[1] : box[3], box[0] : box[2]]
        card = cv2.copyMakeBorder(
            card,
            CARD_PAD_PX,
            CARD_PAD_PX,
            CARD_PAD_PX,
            CARD_PAD_PX,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )
        rotation = cv2.getRotationMatrix2D(
            (card.shape[1] / 2, card.shape[0] / 2), angle, 1.0
        )
        cards.append(
            cv2.warpAffine(
                card,
                rotation,
                (card.shape[1], card.shape[0]),
                borderValue=(255, 255, 255),
            )
        )
    return cards


def scatter(cards: list[np.ndarray], order: list[int]) -> np.ndarray:
    """Kartları tek bir tarayıcı camına, verilen sırayla yerleştirir."""
    height = max(card.shape[0] for card in cards)
    width = max(card.shape[1] for card in cards)
    columns = 2
    rows = (len(order) + columns - 1) // columns
    canvas = np.full(
        (rows * (height + 40) + 40, columns * (width + 40) + 40, 3), 255, dtype=np.uint8
    )
    for position, index in enumerate(order):
        card = cards[index]
        top = 40 + (position // columns) * (height + 40)
        left = 40 + (position % columns) * (width + 40)
        canvas[top : top + card.shape[0], left : left + card.shape[1]] = card
    return canvas


# ---------------------------------------------------------------------------
# Marker kimliği
# ---------------------------------------------------------------------------


def test_markers_carry_the_frame_index_on_normal_projects():
    """Kart tek başına hangi kare olduğunu söyleyebilmeli."""
    assert uses_global_marker_ids(1)
    assert uses_global_marker_ids(cell_marker_capacity())
    assert not uses_global_marker_ids(cell_marker_capacity() + 1)


# ---------------------------------------------------------------------------
# Tanıma
# ---------------------------------------------------------------------------


def test_shuffled_rotated_cards_map_to_the_right_frames(tmp_path):
    """Karışık ve döndürülmüş kartlar doğru kareye eşlenmeli — kabul koşulu."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    page = _page_raster(pdf_path, 1)  # 2. sayfa: 4..7 numaralı kareler
    cards = cut_cards(page, project, angles=[-9.0, 4.0, 11.0, -3.0])
    canvas = scatter(cards, order=[2, 0, 3, 1])

    found = extract_loose_cards(canvas, project, source_name="kartlar.png", cell_dpi=PRINT_DPI)
    assert len(found) == 4

    templates = build_templates(8)
    for card in found:
        identified, _ = identify_frame(card.image, templates)
        assert identified == card.frame_index, (
            f"{card.cell_id}: beklenen {card.frame_index}, bulunan {identified}"
        )


def test_cards_know_which_page_they_came_from(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    page = _page_raster(pdf_path, 1)
    canvas = scatter(cut_cards(page, project, angles=[0.0] * 4), order=[0, 1, 2, 3])

    found = extract_loose_cards(canvas, project, cell_dpi=PRINT_DPI)
    assert {card.page_no for card in found} == {2}
    assert sorted(card.frame_index for card in found) == [4, 5, 6, 7]


def test_cards_from_different_pages_can_share_one_scan(tmp_path):
    """İki sayfanın kartları aynı camda olabilir; her biri kendini tanıtır."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    first = cut_cards(_page_raster(pdf_path, 0), project, angles=[2.0, -5.0, 0.0, 6.0])
    second = cut_cards(_page_raster(pdf_path, 1), project, angles=[-7.0, 3.0, 5.0, -2.0])
    canvas = scatter(first + second, order=[5, 0, 7, 2, 4, 1, 6, 3])

    found = extract_loose_cards(canvas, project, cell_dpi=PRINT_DPI)
    assert sorted(card.frame_index for card in found) == list(range(8))
    assert {card.page_no for card in found} == {1, 2}


def test_a_single_card_is_enough(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    cards = cut_cards(_page_raster(pdf_path, 0), project, angles=[8.0] * 4)
    found = extract_loose_cards(cards[2], project, cell_dpi=PRINT_DPI)

    assert len(found) == 1
    assert found[0].frame_index == 2


def test_extracted_card_has_the_expected_shape(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    cards = cut_cards(_page_raster(pdf_path, 0), project, angles=[0.0] * 4)
    found = extract_loose_cards(cards[0], project, cell_dpi=PRINT_DPI, bleed_mm=0.0)

    from mixedmedia.core.cell_extract import cell_image_rect

    rect = cell_image_rect(project.pages[0].cells[0], project)
    assert found[0].image.shape[1] == pytest.approx(mm_to_px(rect.w_mm, PRINT_DPI), abs=2)
    assert found[0].image.shape[0] == pytest.approx(mm_to_px(rect.h_mm, PRINT_DPI), abs=2)


def test_blank_scan_reports_a_usable_error(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    blank = np.full((800, 600, 3), 255, dtype=np.uint8)

    with pytest.raises(MarkerDetectionFailed) as exc:
        extract_loose_cards(blank, project, source_name="bos.png")
    assert "kart işareti bulunamadı" in str(exc.value)


# ---------------------------------------------------------------------------
# Boru hattına bağlanması
# ---------------------------------------------------------------------------


def test_ingest_falls_back_to_card_mode(tmp_path):
    """Sayfa fiducial'ı yoksa boru hattı kart moduna geçmeli."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    cards = cut_cards(_page_raster(pdf_path, 0), project, angles=[3.0, -6.0, 1.0, 8.0])
    canvas = scatter(cards, order=[3, 1, 0, 2])
    scan_path = tmp_path / "scans" / "kartlar.png"
    scan_path.parent.mkdir(parents=True)
    cv2.imencode(".png", canvas)[1].tofile(scan_path)

    state = pipeline.run_ingest(workspace, project, [scan_path])

    assert [page.method for page in state.pages] == ["cards"]
    assert state.found_indices == {0, 1, 2, 3}
    assert any("kart" in warning for warning in state.pages[0].warnings)


def test_card_mode_can_be_switched_off(tmp_path):
    """Kart modu kapatılabilmeli; kapalıyken tarama kart olarak işlenmemeli.

    Merdivenin son basamağı (yalnızca hücre işaretleriyle sayfa kurma) hâlâ
    denenir — ama sonuç düşük güvenle işaretlenir, yani kullanıcı uyarılır.
    """
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    cards = cut_cards(_page_raster(pdf_path, 0), project, angles=[0.0] * 4)
    scan_path = tmp_path / "scans" / "kartlar.png"
    scan_path.parent.mkdir(parents=True)
    cv2.imencode(".png", scatter(cards, order=[0, 1, 2, 3]))[1].tofile(scan_path)

    state = pipeline.run_ingest(workspace, project, [scan_path], allow_cut_cards=False)
    assert "cards" not in {page.method for page in state.pages}
    assert all(page.confidence == "low" for page in state.pages)


def test_card_mode_is_preferred_over_a_cell_marker_page_fit(tmp_path):
    """Dağınık kartlar "sayfa" sanılmamalı.

    Dört nokta kümesini bir homografi her zaman iyi uydurur, yani artığa
    bakarak sayfayı karttan ayırt edemeyiz. Ayrım köşe işaretlerinin
    varlığına dayanır: sayfayı onlar tanımlar.
    """
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    cards = cut_cards(_page_raster(pdf_path, 0), project, angles=[0.0] * 4)
    scan_path = tmp_path / "scans" / "kartlar.png"
    scan_path.parent.mkdir(parents=True)
    cv2.imencode(".png", scatter(cards, order=[0, 1, 2, 3]))[1].tofile(scan_path)

    state = pipeline.run_ingest(workspace, project, [scan_path])
    assert [page.method for page in state.pages] == ["cards"]
    assert state.found_indices == {0, 1, 2, 3}


def test_full_pages_still_take_the_page_path(tmp_path):
    """Sayfa bütünse kart moduna düşülmemeli."""
    from .test_roundtrip import render_and_scan

    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    state = pipeline.run_ingest(workspace, project, scans)
    assert all(page.method.startswith("corners") for page in state.pages)
    assert state.found_indices == set(range(8))


def test_cards_and_full_pages_can_be_mixed(tmp_path):
    """Bazı sayfalar kesilmiş, bazıları bütün olabilir."""
    from .test_roundtrip import render_and_scan

    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    scans_dir = tmp_path / "scans"
    render_and_scan(pdf_path, scans_dir, flip_page=None, skip_pages={2})

    cards = cut_cards(_page_raster(pdf_path, 1), project, angles=[4.0, -8.0, 2.0, 0.0])
    cv2.imencode(".png", scatter(cards, order=[1, 3, 0, 2]))[1].tofile(
        scans_dir / "z_kartlar.png"
    )

    state = pipeline.run_ingest(workspace, project, [scans_dir])
    methods = {page.method for page in state.pages}
    assert "cards" in methods
    assert any(method.startswith("corners") for method in methods)
    assert state.found_indices == set(range(8))
