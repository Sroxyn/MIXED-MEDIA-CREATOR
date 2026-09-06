"""EXPORT boru hattının birim testleri.

Uçtan uca doğrulama ``test_roundtrip.py``'de, stabilizasyon
``test_stabilize.py``'de; burada tek tek parçalar ve ffmpeg gerektirmeyen
yollar sınanır.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from mixedmedia.core import pdf_writer, pipeline, scan_ingest
from mixedmedia.core.encode import build_encode_command
from mixedmedia.core.errors import MixedMediaError
from mixedmedia.core.frame_normalize import (
    apply_lut,
    calibration_lut,
    resize_to,
    white_point_lut,
)
from mixedmedia.core.models import Codec, MissingFramePolicy
from mixedmedia.core.rectify import rectify_scan

from .conftest import build_project
from .test_roundtrip import render_and_scan

# ---------------------------------------------------------------------------
# Tarama girişi
# ---------------------------------------------------------------------------


def test_iter_scans_reads_a_multipage_pdf(tmp_path):
    """Çok sayfalı PDF sayfa sayfa raster'a çevrilmeli (§7.1)."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)

    scans = list(scan_ingest.iter_scans([pdf_path], render_dpi=150))
    assert len(scans) == 2
    assert [scan.name for scan in scans] == ["baski.pdf#1", "baski.pdf#2"]
    for scan in scans:
        assert scan.bgr.ndim == 3 and scan.bgr.shape[2] == 3
        assert scan.dpi_hint == 150.0


def test_iter_scans_walks_a_directory(tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    for index in range(3):
        image = np.full((40, 30, 3), 200, dtype=np.uint8)
        cv2.imencode(".png", image)[1].tofile(folder / f"s{index}.png")
    (folder / "notlar.txt").write_text("desteklenmeyen", encoding="utf-8")

    scans = list(scan_ingest.iter_scans([folder]))
    assert [scan.name for scan in scans] == ["s0.png", "s1.png", "s2.png"]


def test_missing_scan_path_is_a_user_facing_error(tmp_path):
    with pytest.raises(MixedMediaError) as exc:
        scan_ingest.collect_scan_paths([tmp_path / "yok"])
    assert "bulunamadı" in str(exc.value)


def test_empty_directory_is_a_user_facing_error(tmp_path):
    (tmp_path / "bos").mkdir()
    with pytest.raises(MixedMediaError):
        scan_ingest.collect_scan_paths([tmp_path / "bos"])


def test_scan_paths_with_turkish_characters_are_read(tmp_path):
    """cv2.imread Türkçe yollarda başarısız olur; okuyucu bunu aşmalı."""
    folder = tmp_path / "çalışma ığü"
    folder.mkdir()
    target = folder / "şeker.png"
    cv2.imencode(".png", np.full((20, 30, 3), 128, dtype=np.uint8))[1].tofile(target)

    scans = list(scan_ingest.iter_scans([target]))
    assert len(scans) == 1
    assert scans[0].bgr.shape == (20, 30, 3)


# ---------------------------------------------------------------------------
# Normalizasyon
# ---------------------------------------------------------------------------


def test_calibration_strip_removes_a_colour_cast(tmp_path):
    """Şerit nötr gridir; kanal sapması doğrudan tarayıcının renk kaymasıdır."""
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    bgr = cv2.imdecode(np.fromfile(scans[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    page = rectify_scan(bgr, project, work_dpi=300)

    def channel_medians(image):
        return [float(np.median(image[:, :, i])) for i in range(3)]

    before = channel_medians(page.image)
    assert max(before) - min(before) > 10, "Test taraması renk kaymalı olmalı"

    lut = calibration_lut(page, project.layout.footer_text)
    assert lut is not None, "Kalibrasyon şeridi okunamadı"
    assert lut.shape == (1, 256, 3), "LUT kanal başına olmalı"

    after = channel_medians(apply_lut(page.image, lut))
    assert max(after) - min(after) <= 2, f"Renk kayması sürüyor: {after}"


def test_white_point_lut_is_per_channel():
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[:, :, 0] = 200  # mavi sönük
    image[:, :, 1] = 250
    image[:, :, 2] = 250

    corrected = apply_lut(image, white_point_lut(image))
    medians = [float(np.median(corrected[:, :, i])) for i in range(3)]
    assert max(medians) - min(medians) <= 2


def test_apply_lut_expands_single_channel_table():
    image = np.full((4, 4, 3), 100, dtype=np.uint8)
    table = np.clip(np.arange(256) * 2, 0, 255).astype(np.uint8).reshape(1, 256, 1)
    assert apply_lut(image, table)[0, 0].tolist() == [200, 200, 200]


def test_apply_lut_without_table_is_identity():
    image = np.full((4, 4, 3), 77, dtype=np.uint8)
    assert np.array_equal(apply_lut(image, None), image)


def test_resize_to_reaches_exact_size():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    assert resize_to(image, (320, 180)).shape == (180, 320, 3)
    same = np.zeros((180, 320, 3), dtype=np.uint8)
    assert resize_to(same, (320, 180)) is same


# ---------------------------------------------------------------------------
# Kodlama komutu
# ---------------------------------------------------------------------------


def test_h264_command_matches_spec():
    cmd = build_encode_command(
        "ffmpeg", Path("in/%05d.png"), Path("out.mp4"), 6, Codec.H264
    )
    assert "libx264" in cmd
    assert cmd[cmd.index("-crf") + 1] == "16"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-framerate") + 1] == "6"
    assert cmd[-1] == "out.mp4"


def test_prores_command_uses_422_hq():
    cmd = build_encode_command(
        "ffmpeg", Path("in/%05d.png"), Path("out.mov"), 6, Codec.PRORES
    )
    assert "prores_ks" in cmd
    assert cmd[cmd.index("-profile:v") + 1] == "3"


def test_audio_is_mapped_from_the_original_video():
    cmd = build_encode_command(
        "ffmpeg",
        Path("in/%05d.png"),
        Path("out.mp4"),
        6,
        Codec.H264,
        audio_source=Path("orig.mp4"),
    )
    assert cmd[cmd.index("-map") + 1] == "0:v"
    assert "1:a" in cmd
    assert "-shortest" in cmd


def test_png_seq_is_refused_by_the_ffmpeg_command_builder():
    with pytest.raises(MixedMediaError):
        build_encode_command(
            "ffmpeg", Path("in/%05d.png"), Path("out"), 6, Codec.PNG_SEQ
        )


# ---------------------------------------------------------------------------
# Export boru hattı (ffmpeg'siz yol)
# ---------------------------------------------------------------------------


def test_run_export_writes_a_png_sequence(tmp_path):
    """PNG sekansı çıktısı ffmpeg'e uğramaz — boru hattını tek başına sınar."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)
    pipeline.run_ingest(workspace, project, scans)

    out_dir = tmp_path / "seq"
    written, report = pipeline.run_export(
        workspace,
        project,
        out_dir,
        codec=Codec.PNG_SEQ,
        resolution=(160, 90),
        stabilize=0.0,
    )

    assert written == out_dir
    files = sorted(out_dir.glob("*.png"))
    assert len(files) == 8
    assert report.is_complete
    assert report.output_length == 8

    first = cv2.imdecode(np.fromfile(files[0], dtype=np.uint8), cv2.IMREAD_COLOR)
    assert first.shape == (90, 160, 3)


def test_run_export_before_ingest_is_refused(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    with pytest.raises(MixedMediaError) as exc:
        pipeline.run_export(workspace, project, tmp_path / "out", codec=Codec.PNG_SEQ)
    assert "mm ingest" in str(exc.value)


def test_ingest_without_layout_is_refused(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    project.pages = []
    with pytest.raises(MixedMediaError) as exc:
        pipeline.run_ingest(workspace, project, [tmp_path])
    assert "project.mmp.json" in str(exc.value)


def test_ingest_state_survives_a_save_and_reload(tmp_path):
    """İş yarıda kalırsa kaldığı yerden devam edebilmeli (§8)."""
    from mixedmedia.core.project import load_project

    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)
    state = pipeline.run_ingest(workspace, project, scans, bleed_mm=-1.0)

    _, reloaded = load_project(workspace.root)
    assert reloaded.ingest is not None
    assert reloaded.ingest.bleed_mm == -1.0
    assert reloaded.ingest.found_indices == state.found_indices
    assert [page.page_no for page in reloaded.ingest.pages] == [1]


def test_bleed_changes_the_extracted_crop_size(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, tmp_path / "scans", flip_page=None)

    tight = pipeline.run_ingest(workspace, project, scans, bleed_mm=-2.0)
    tight_size = cv2.imdecode(
        np.fromfile(workspace.resolve(tight.cells[0].file), dtype=np.uint8), cv2.IMREAD_COLOR
    ).shape

    loose = pipeline.run_ingest(workspace, project, scans, bleed_mm=2.0)
    loose_size = cv2.imdecode(
        np.fromfile(workspace.resolve(loose.cells[0].file), dtype=np.uint8), cv2.IMREAD_COLOR
    ).shape

    assert loose_size[0] > tight_size[0]
    assert loose_size[1] > tight_size[1]


def test_sequence_report_summary_is_readable():
    from mixedmedia.core.sequencer import build_sequence

    available = {0: Path("a.png"), 1: Path("b.png")}
    _, report = build_sequence(available, 4, MissingFramePolicy.HOLD)
    assert "2/4" in report.summary()
    assert not report.is_complete


def test_pdf_page_count_matches_scan_count(tmp_path):
    """Basılan sayfa sayısı ile taranacak sayfa sayısı tutmalı."""
    workspace, project = build_project(tmp_path / "p", frame_count=7, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    with pymupdf.open(pdf_path) as doc:
        assert doc.page_count == len(project.pages) == 2
