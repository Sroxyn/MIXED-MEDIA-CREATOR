"""GUI testleri (bkz. CLAUDE.md §8).

Qt ``offscreen`` platformunda koşar; ekran gerektirmez. Bu platformda font
veritabanı boş olduğu için metinler kutu olarak çizilir — testler bu yüzden
görünüşü değil **davranışı** doğrular: sihirbaz kuruluyor mu, ayarlar projeye
yazılıyor mu, uzun işler ana thread'i bloke etmiyor mu.
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mixedmedia.core import pdf_writer, pipeline  # noqa: E402
from mixedmedia.core.errors import MixedMediaError, OperationCancelled  # noqa: E402
from mixedmedia.core.models import MissingFramePolicy, Orientation  # noqa: E402
from mixedmedia.ui.session import Session, slugify  # noqa: E402
from mixedmedia.ui.workers import Worker, numpy_to_qimage, pil_to_qimage  # noqa: E402

from .conftest import build_project, make_frame  # noqa: E402
from .test_roundtrip import render_and_scan  # noqa: E402


def pump(app: QApplication, seconds: float = 0.5) -> None:
    """Olay döngüsünü kısa süre çevirir (arka plan işleri yetişsin diye)."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# Oturum
# ---------------------------------------------------------------------------


def test_session_round_trips_through_disk(tmp_path):
    workspace, project = build_project(tmp_path / "p", frame_count=4)
    session = Session.open(workspace.root)

    assert session.project.project_id == project.project_id
    session.project.name = "Yeni Ad"
    session.save()

    reopened = Session.open(workspace.root)
    assert reopened.project.name == "Yeni Ad"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Kedi Sekansı", "kedi_sekansi"),
        ("Çığlık — Ağustos", "ciglik_agustos"),
        ("ŞÜKRÜ", "sukru"),
        ("!!!", "proje"),
    ],
)
def test_slugify_handles_turkish(name, expected):
    assert slugify(name) == expected


def test_session_default_paths_follow_project_name(tmp_path):
    workspace, _ = build_project(tmp_path / "p", frame_count=2, name="Kedi Sekansı")
    session = Session.open(workspace.root)
    assert session.default_pdf_path().name == "kedi_sekansi.pdf"
    assert session.default_video_path().name == "kedi_sekansi_final.mp4"


# ---------------------------------------------------------------------------
# Worker — uzun işler ana thread'i bloke etmemeli
# ---------------------------------------------------------------------------


def test_worker_reports_progress_and_result(qapp):
    seen: list[tuple[int, int, str]] = []
    results: list[object] = []

    def job(report, cancel):
        for index in range(3):
            report(index + 1, 3, "adım")
        return "bitti"

    worker = Worker(job)
    worker.progressed.connect(lambda *args: seen.append(args))
    worker.succeeded.connect(results.append)
    worker.start()
    pump(qapp)

    assert results == ["bitti"]
    assert seen[-1] == (3, 3, "adım")


def test_worker_surfaces_user_facing_errors(qapp):
    messages: list[str] = []

    def job(report, cancel):
        raise MixedMediaError("Anlaşılır bir hata")

    worker = Worker(job)
    worker.failed.connect(messages.append)
    worker.start()
    pump(qapp)

    assert messages and "Anlaşılır bir hata" in messages[0]


def test_worker_cancel_reaches_the_job(qapp):
    cancelled: list[bool] = []

    def job(report, cancel):
        while not cancel.is_set():
            time.sleep(0.01)
        raise OperationCancelled("Test")

    worker = Worker(job)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.start()
    pump(qapp, 0.1)
    worker.cancel()
    pump(qapp)

    assert cancelled == [True]


def test_worker_runs_off_the_main_thread(qapp):
    thread_ids: list[int] = []

    def job(report, cancel):
        thread_ids.append(threading.get_ident())
        return None

    worker = Worker(job)
    worker.start()
    pump(qapp)

    assert thread_ids and thread_ids[0] != threading.get_ident()


# ---------------------------------------------------------------------------
# Görüntü dönüşümleri
# ---------------------------------------------------------------------------


def test_pil_to_qimage_preserves_size_and_pixels(qapp):
    frame = make_frame(7)
    image = pil_to_qimage(frame)
    assert (image.width(), image.height()) == frame.size
    # Köşe pikseli beyaz zemin üzerindeki siyah çerçeveden gelir.
    assert image.pixelColor(0, 0).value() < 128
    frame.close()


def test_numpy_to_qimage_swaps_bgr_to_rgb(qapp):
    import numpy as np

    array = np.zeros((4, 4, 3), dtype=np.uint8)
    array[:, :, 2] = 255  # BGR'de kırmızı
    image = numpy_to_qimage(array)
    colour = image.pixelColor(0, 0)
    assert (colour.red(), colour.green(), colour.blue()) == (255, 0, 0)


# ---------------------------------------------------------------------------
# IMPORT sihirbazı
# ---------------------------------------------------------------------------


@pytest.fixture
def import_wizard(qapp, tmp_path):
    from mixedmedia.ui.import_wizard import ImportWizard

    workspace, _ = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    session = Session.open(workspace.root)
    wizard = ImportWizard(session)
    yield wizard, session
    wizard.close()


def test_import_wizard_has_the_four_steps(import_wizard):
    wizard, _ = import_wizard
    titles = [wizard.page(pid).title() for pid in wizard.pageIds()]
    assert titles == ["Video", "Görünüm", "Sayfa düzeni", "Çıktı"]


def test_layout_page_writes_settings_into_the_project(import_wizard, qapp):
    wizard, session = import_wizard
    page = wizard.layout_page
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 0.3)

    page.manual_mode.setChecked(True)
    page.cols.setValue(3)
    page.rows.setValue(4)
    page.margin.setValue(15)
    page.gutter.setValue(8)
    pump(qapp, 0.3)

    layout = session.project.layout
    assert (layout.cols, layout.rows) == (3, 4)
    assert layout.margin_mm == 15
    assert layout.gutter_mm == 8
    assert len(session.project.pages) == 1  # 8 kare, 12 hücre/sayfa


def test_layout_page_auto_mode_picks_a_grid(import_wizard, qapp):
    wizard, session = import_wizard
    page = wizard.layout_page
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 0.3)

    page.auto_mode.setChecked(True)
    page.per_page.setCurrentIndex(page.per_page.findData(4))
    pump(qapp, 0.3)

    layout = session.project.layout
    assert layout.cells_per_page == 4
    # 16:9 kaynak için yatay sayfa daha çok alan verir.
    assert layout.orientation is Orientation.LANDSCAPE


def test_layout_page_reports_an_impossible_grid_instead_of_crashing(import_wizard, qapp):
    wizard, session = import_wizard
    page = wizard.layout_page
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 0.3)

    page.manual_mode.setChecked(True)
    page.margin.setValue(40)
    page.gutter.setValue(40)
    page.cols.setValue(12)
    page.rows.setValue(12)
    pump(qapp, 0.3)

    assert "sığmıyor" in page.info.text()


def test_layout_page_warns_when_markers_are_disabled(import_wizard, qapp):
    wizard, _ = import_wizard
    page = wizard.layout_page
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 0.3)

    page.markers.setChecked(False)
    pump(qapp, 0.2)
    assert "manuel hizalama" in page.marker_warning.text()


def test_look_page_writes_processing_settings(import_wizard, qapp):
    wizard, session = import_wizard
    wizard.setStartId(wizard.pageIds()[1])
    wizard.restart()
    pump(qapp, 0.3)

    page = wizard.look_page
    page.density.setValue(30)
    page.gamma.setValue(1.5)
    pump(qapp, 0.3)

    assert session.project.processing.print_density == pytest.approx(0.30, abs=1e-6)
    assert session.project.processing.gamma == pytest.approx(1.5, abs=1e-6)


def test_layout_preview_renders_a_page(import_wizard, qapp):
    wizard, session = import_wizard
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 1.5)

    pixmap = wizard.layout_page.preview.view._pixmap
    assert pixmap is not None and not pixmap.isNull()

    # Önizleme, projedeki kağıt oranını birebir yansıtmalı.
    expected_ratio = 210 / 297
    if session.project.layout.orientation is Orientation.LANDSCAPE:
        expected_ratio = 297 / 210
    assert pixmap.width() / pixmap.height() == pytest.approx(expected_ratio, rel=0.01)


def test_layout_page_keeps_a_manual_grid_when_revisited(import_wizard, qapp):
    """Elle kurulan ızgara, sihirbaza dönünce otomatikle ezilmemeli."""
    wizard, session = import_wizard
    session.project.layout.cols = 2
    session.project.layout.rows = 2
    session.project.layout.orientation = Orientation.PORTRAIT
    session.save()

    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 0.5)

    assert wizard.layout_page.manual_mode.isChecked()
    assert session.project.layout.orientation is Orientation.PORTRAIT
    assert (session.project.layout.cols, session.project.layout.rows) == (2, 2)


# ---------------------------------------------------------------------------
# EXPORT sihirbazı
# ---------------------------------------------------------------------------


@pytest.fixture
def ingested(tmp_path):
    """Taramaları alınmış, videoya hazır bir proje."""
    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    pdf_path = tmp_path / "baski.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scans = render_and_scan(pdf_path, workspace.scans_dir, flip_page=None)
    pipeline.run_ingest(workspace, project, scans)
    from mixedmedia.core.project import save_project

    save_project(workspace, project)
    return Session.open(workspace.root)


@pytest.fixture
def export_wizard(qapp, ingested):
    from mixedmedia.ui.export_wizard import ExportWizard

    wizard = ExportWizard(ingested)
    yield wizard, ingested
    wizard.close()


def test_export_wizard_has_the_four_steps(export_wizard):
    wizard, _ = export_wizard
    titles = [wizard.page(pid).title() for pid in wizard.pageIds()]
    assert titles == ["Proje", "Taramalar", "Kareler", "Video"]


def test_project_page_accepts_an_already_open_project(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.restart()
    pump(qapp, 0.2)
    assert wizard.project_page.isComplete()
    assert session.project.name in wizard.project_page.summary.text()


def test_scans_page_shows_a_badge_per_page(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.setStartId(wizard.pageIds()[1])
    wizard.restart()
    pump(qapp, 0.5)

    table = wizard.scans_page.table
    assert table.rowCount() == len(session.project.pages)
    # Durum sütunu renkli bir rozet widget'ı taşır, düz metin değil.
    badge = table.cellWidget(0, 1)
    assert badge is not None and "iyi" in badge.text()
    assert "8/8" in wizard.scans_page.summary.text()


def test_frames_page_fills_the_filmstrip(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 1.0)

    page = wizard.frames_page
    assert page.filmstrip.count() == session.project.extraction.frame_count
    assert len(page._slots) == session.project.extraction.frame_count
    assert "8/8" in page.status.text()


def test_frames_page_policy_change_reshapes_the_sequence(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 1.0)

    # Bir kareyi sil: politika artık fark yaratmalı.
    missing = session.workspace.resolve(session.project.ingest.cells[3].file)
    missing.unlink()

    page = wizard.frames_page
    page.policy.setCurrentIndex(page.policy.findData(MissingFramePolicy.SKIP))
    pump(qapp, 0.3)
    assert len(page._slots) == 7

    page.policy.setCurrentIndex(page.policy.findData(MissingFramePolicy.HOLD))
    pump(qapp, 0.3)
    assert len(page._slots) == 8


def test_frames_page_saves_settings_on_leaving(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.setStartId(wizard.pageIds()[2])
    wizard.restart()
    pump(qapp, 1.0)

    page = wizard.frames_page
    page.stabilize.setValue(0)
    page.policy.setCurrentIndex(page.policy.findData(MissingFramePolicy.PLACEHOLDER))
    assert page.validatePage()

    assert session.project.export.stabilize == 0.0
    assert session.project.export.missing_frame_policy is MissingFramePolicy.PLACEHOLDER


def test_video_page_warns_about_a_speed_change(export_wizard, qapp):
    wizard, _ = export_wizard
    wizard.setStartId(wizard.pageIds()[3])
    wizard.restart()
    pump(qapp, 0.3)

    page = wizard.video_page
    page.fps.setValue(24.0)
    pump(qapp, 0.2)
    assert "hızlanacak" in page.speed_note.text()

    page.fps.setValue(3.0)
    pump(qapp, 0.2)
    assert "yavaşlayacak" in page.speed_note.text()


def test_video_page_defaults_to_source_resolution(export_wizard, qapp):
    wizard, session = export_wizard
    wizard.setStartId(wizard.pageIds()[3])
    wizard.restart()
    pump(qapp, 0.3)

    page = wizard.video_page
    source = session.project.source
    assert (page.width.value(), page.height.value()) == (
        source.display_width,
        source.display_height,
    )


def test_video_page_disables_audio_without_a_soundtrack(export_wizard, qapp):
    wizard, _ = export_wizard
    wizard.setStartId(wizard.pageIds()[3])
    wizard.restart()
    pump(qapp, 0.3)
    assert not wizard.video_page.audio.isEnabled()


# ---------------------------------------------------------------------------
# Ana pencere
# ---------------------------------------------------------------------------


def test_main_window_enables_actions_once_a_project_is_open(qapp, tmp_path):
    from mixedmedia.ui.app import MainWindow

    workspace, _ = build_project(tmp_path / "p", frame_count=4)
    window = MainWindow()
    assert not window.import_button.isEnabled()
    assert not window.export_button.isEnabled()

    window._loadProject(workspace.root)
    assert window.import_button.isEnabled()
    assert window.export_button.isEnabled()
    window.close()


def test_main_window_reports_paper_and_frame_counts(qapp, tmp_path):
    from mixedmedia.ui.app import MainWindow

    workspace, project = build_project(tmp_path / "p", frame_count=8, cols=2, rows=2)
    window = MainWindow()
    window._loadProject(workspace.root)
    text = window.status.text()
    assert project.name in text
    assert "8 kare" in text
    assert f"{len(project.pages)} sayfa" in text
    window.close()


# ---------------------------------------------------------------------------
# Manuel hizalama ekranı (M6, CLAUDE.md §7.4)
# ---------------------------------------------------------------------------


def _markerless_scan(tmp_path):
    """İşaretsiz basılmış bir sayfa ve köşelerinin gerçek yeri."""
    import cv2

    from mixedmedia.core import pdf_writer

    from .test_manual_align import _page_raster, scan_with_known_corners

    workspace, project = build_project(
        tmp_path / "p",
        frame_count=8,
        cols=2,
        rows=2,
        markers_enabled=False,
        calibration_strip=False,
    )
    pdf_path = tmp_path / "b.pdf"
    pdf_writer.write_pdf(project, workspace.root, pdf_path)
    scan, corners = scan_with_known_corners(_page_raster(pdf_path, 0))
    assert cv2 is not None
    return workspace, project, scan, corners


def test_align_dialog_starts_with_four_corners(qapp, tmp_path):
    from mixedmedia.ui.export_wizard.align_dialog import AlignDialog

    _, project, scan, _ = _markerless_scan(tmp_path)
    dialog = AlignDialog("scan_001.png", scan, project)
    pump(qapp, 0.2)

    assert len(dialog.editor.corners()) == 4
    assert dialog.appliesToRest()
    dialog.close()


def test_align_dialog_returns_what_the_user_set(qapp, tmp_path):
    from mixedmedia.ui.export_wizard.align_dialog import AlignDialog

    _, project, scan, corners = _markerless_scan(tmp_path)
    dialog = AlignDialog("scan_001.png", scan, project)
    dialog.editor.setCorners(corners)
    dialog.offset_x.setValue(2.5)
    dialog.page.setCurrentIndex(dialog.page.findData(2))
    pump(qapp, 0.2)

    alignment = dialog.alignment()
    assert len(alignment.corners) == 4
    assert alignment.page_no == 2
    assert alignment.grid_offset_mm[0] == pytest.approx(2.5)
    dialog.close()


def test_align_dialog_restores_a_saved_alignment(qapp, tmp_path):
    """Bir kez yapılan hizalama pencereye geri yüklenmeli."""
    from mixedmedia.core.models import ManualAlignment
    from mixedmedia.ui.export_wizard.align_dialog import AlignDialog

    _, project, scan, corners = _markerless_scan(tmp_path)
    saved = ManualAlignment(corners=corners, page_no=2, grid_offset_mm=(-3.0, 1.5))

    dialog = AlignDialog("scan_001.png", scan, project, existing=saved)
    pump(qapp, 0.2)

    assert dialog.page.currentData() == 2
    assert dialog.offset_x.value() == pytest.approx(-3.0)
    assert dialog.offset_y.value() == pytest.approx(1.5)
    for got, expected in zip(dialog.editor.corners(), corners):
        assert got[0] == pytest.approx(expected[0], abs=0.5)
    dialog.close()


def test_grid_editor_maps_widget_clicks_back_to_image_pixels(qapp, tmp_path):
    """Sürükleme doğru piksele düşmeli; ölçek dönüşümü tersinir olmalı."""
    from PySide6.QtCore import QPointF

    from mixedmedia.core.layout import grid_from_settings
    from mixedmedia.ui.widgets.grid_editor import GridEditor

    _, project, scan, _ = _markerless_scan(tmp_path)
    editor = GridEditor()
    editor.setScan(scan, grid_from_settings(project.layout))
    editor.resize(600, 800)
    pump(qapp, 0.2)

    for point in ([0.0, 0.0], [scan.shape[1] / 2, scan.shape[0] / 3]):
        widget_point = editor._toWidget(point)
        back = editor._toImage(QPointF(widget_point))
        assert back[0] == pytest.approx(point[0], abs=1.0)
        assert back[1] == pytest.approx(point[1], abs=1.0)
    editor.close()


def test_grid_editor_reports_corner_changes(qapp, tmp_path):
    from mixedmedia.core.layout import grid_from_settings
    from mixedmedia.ui.widgets.grid_editor import GridEditor

    _, project, scan, corners = _markerless_scan(tmp_path)
    editor = GridEditor()
    editor.setScan(scan, grid_from_settings(project.layout))

    seen = []
    editor.cornersChanged.connect(seen.append)
    editor.setCorners(corners)
    pump(qapp, 0.1)

    assert seen and len(seen[-1]) == 4
    editor.close()
