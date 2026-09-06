"""Sessiz başarısızlıklara karşı korumalar.

Buradaki testler, sistemin "başardım" diyip çöp ürettiği durumları kilitler.
Round-trip testi bunları yakalayamaz: şablon eşleşmesi parlaklık ve kontrasta
duyarsızdır, bu yüzden bozuk normalize edilmiş bir kare de eşleşebilir.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from mixedmedia.core import pdf_writer, pipeline
from mixedmedia.core.encode import build_encode_command
from mixedmedia.core.errors import MixedMediaError
from mixedmedia.core.frame_normalize import apply_lut, calibration_lut
from mixedmedia.core.layout import layout_signature
from mixedmedia.core.models import Codec, MissingFramePolicy, Orientation, PaperSize
from mixedmedia.core.rectify import rectify_scan
from mixedmedia.core.sequencer import build_sequence

from .conftest import build_project
from .test_roundtrip import render_and_scan


# ---------------------------------------------------------------------------
# Baskı sonrası yerleşim değişikliği
# ---------------------------------------------------------------------------


def test_layout_signature_ignores_fields_that_do_not_move_cells():
    """DPI ve kesim işareti biçimi hücre yerlerini değiştirmez."""
    from mixedmedia.core.models import CutMarks, LayoutSettings

    base = LayoutSettings(cols=2, rows=3)
    same = LayoutSettings(cols=2, rows=3, dpi=600, cut_marks=CutMarks.HAIRLINE)
    assert layout_signature(base) == layout_signature(same)


@pytest.mark.parametrize(
    "change",
    [
        {"orientation": Orientation.LANDSCAPE},
        {"paper": PaperSize.A3},
        {"margin_mm": 20.0},
        {"gutter_mm": 6.0},
        {"cols": 3, "rows": 3},
        {"markers_enabled": False},
        {"footer_text": False},
    ],
)
def test_layout_signature_changes_when_cells_move(change):
    from mixedmedia.core.models import LayoutSettings

    base = LayoutSettings(cols=2, rows=3)
    altered = LayoutSettings(**{**{"cols": 2, "rows": 3}, **change})
    assert layout_signature(base) != layout_signature(altered)


def test_writing_a_pdf_records_the_printed_layout(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    assert project.printed_signature is None

    pdf_writer.write_pdf(project, workspace.root, tmp_path / "b.pdf")
    assert project.printed_signature == layout_signature(project.layout)


def test_page_pngs_also_record_the_printed_layout(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_writer.write_page_pngs(project, workspace.root, tmp_path / "png", dpi=96)
    assert project.printed_signature == layout_signature(project.layout)


def test_ingest_refuses_scans_printed_with_a_different_layout(tmp_path):
    """Yerleşim baskıdan sonra değişirse export sessizce çöp üretmemeli.

    Fiducial'lar hâlâ bulunur ve sayfa "yüksek güvenle" hizalanır; hata ancak
    kesilen bölgelere bakınca anlaşılır. Bu yüzden açıkça reddediyoruz.
    """
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    # Kullanıcı baskıdan sonra sayfayı yatay çevirdi.
    project.layout.orientation = Orientation.LANDSCAPE

    with pytest.raises(MixedMediaError) as exc:
        pipeline.run_ingest(workspace, project, scans)
    assert "basıldıktan sonra değişmiş" in str(exc.value)


def test_ingest_accepts_scans_when_the_layout_is_unchanged(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    state = pipeline.run_ingest(workspace, project, scans)
    assert state.found_indices == set(range(8))


def test_ingest_still_works_for_projects_without_a_recorded_signature(tmp_path):
    """Eski projelerde imza yok; iş durmamalı, yalnızca varsayım yapılmalı."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    project.printed_signature = None
    state = pipeline.run_ingest(workspace, project, scans)
    assert state.found_indices == set(range(4))


# ---------------------------------------------------------------------------
# Çıkarılan kareler gerçekten kağıt gibi görünmeli
# ---------------------------------------------------------------------------


def test_extracted_cells_look_like_paper_not_mud(tmp_path):
    """Kesilen kareler ağırlıklı olarak beyaz kağıt olmalı.

    Normalizasyon bozulursa kareler koyu ve renk kaymalı çıkar; şablon
    eşleşmesi bunu fark etmez çünkü parlaklığa duyarsızdır.
    """
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=2)

    state = pipeline.run_ingest(workspace, project, scans)

    for cell in state.cells:
        image = cv2.imdecode(
            np.fromfile(workspace.resolve(cell.file), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        medians = [float(np.median(image[:, :, channel])) for channel in range(3)]
        assert min(medians) > 200, f"{cell.cell_id} çok koyu: {medians}"
        assert max(medians) - min(medians) <= 6, f"{cell.cell_id} renk kaymalı: {medians}"


# ---------------------------------------------------------------------------
# Kalibrasyon şeridi yanlış yerdeyse reddedilmeli
# ---------------------------------------------------------------------------


def test_calibration_is_rejected_when_the_strip_is_not_where_expected(tmp_path):
    """Şerit beklenen yerde değilse kurulacak eğri görüntüyü bozar."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, work_dpi=300)
    assert calibration_lut(page, project.layout.footer_text) is not None

    # Şeridin olduğu bölgeyi rastgele gürültüyle ez: artık sıralı gri değil.
    rng = np.random.default_rng(7)
    height = page.image.shape[0]
    band = page.image[int(height * 0.86) :, :, :]
    band[:] = rng.integers(0, 256, band.shape, dtype=np.uint8)

    assert calibration_lut(page, project.layout.footer_text) is None


def test_calibration_is_rejected_on_a_blank_strip(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, work_dpi=300)
    page.image[:] = 255

    assert calibration_lut(page, project.layout.footer_text) is None


def test_white_point_fallback_keeps_paper_bright():
    from mixedmedia.core.frame_normalize import white_point_lut

    image = np.full((32, 32, 3), 200, dtype=np.uint8)
    corrected = apply_lut(image, white_point_lut(image))
    assert float(np.median(corrected)) > 240


# ---------------------------------------------------------------------------
# Enum sınırları — Qt ve CLI düz metin verebilir
# ---------------------------------------------------------------------------


def test_build_sequence_accepts_a_plain_string_policy():
    """Qt'nin ``currentData()``'sı str tabanlı enum'u düz metne çevirebilir.

    Normalleştirmezsek ``is`` karşılaştırmaları sessizce başarısız olur ve
    kullanıcının seçtiği politika hiç uygulanmaz.
    """
    available = {0: Path("a.png"), 2: Path("c.png")}
    slots, report = build_sequence(available, 3, "skip")
    assert [slot.frame_index for slot in slots] == [0, 2]
    assert report.missing == [1]


def test_build_sequence_string_and_enum_agree():
    available = {0: Path("a.png")}
    from_enum, _ = build_sequence(available, 3, MissingFramePolicy.PLACEHOLDER)
    from_str, _ = build_sequence(available, 3, "placeholder")
    assert [slot.resolution for slot in from_enum] == [
        slot.resolution for slot in from_str
    ]


def test_encode_command_accepts_a_plain_string_codec():
    cmd = build_encode_command("ffmpeg", Path("in/%05d.png"), Path("o.mov"), 6, "prores")
    assert "prores_ks" in cmd


def test_encode_rejects_an_unknown_codec():
    with pytest.raises(ValueError):
        build_encode_command("ffmpeg", Path("in/%05d.png"), Path("o.mp4"), 6, "h265")


def test_codec_enum_round_trips_through_its_value():
    for codec in Codec:
        assert Codec(codec.value) is codec
