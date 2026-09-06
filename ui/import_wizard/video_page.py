"""IMPORT adım 1: kaynak videoyu seç ve kareleri çıkar (CLAUDE.md §8)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import frame_extract, layout as layout_mod
from ...core.i18n import t
from ...core.constants import FPS_PRESETS
from ..widgets.common import BusyBar, DropArea, hline
from ..workers import Worker
from .base import VIDEO_SUFFIXES, WizardPage

log = logging.getLogger(__name__)

__all__ = ["VideoPage"]


# ---------------------------------------------------------------------------
# Adım 1 — Video
# ---------------------------------------------------------------------------


class VideoPage(WizardPage):
    """Kaynak videoyu seçer ve kareleri çıkarır."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Video"))
        self.setSubTitle(
            t("Kaynak videoyu bırakın ve kaç kareye böleceğinizi seçin.")
        )
        self._extracted = False

        layout = QVBoxLayout(self)

        self.drop = DropArea(
            t("Videoyu buraya sürükleyin"),
            suffixes=VIDEO_SUFFIXES,
            on_browse=self._browse,
        )
        self.drop.dropped.connect(lambda paths: self._loadVideo(paths[0]))
        layout.addWidget(self.drop)

        self.summary = QLabel(t("Henüz video seçilmedi."))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        layout.addWidget(hline())

        form = QFormLayout()
        self.fps = QComboBox()
        self.fps.setEditable(True)
        for preset in FPS_PRESETS:
            self.fps.addItem(f"{preset}", float(preset))
        self.fps.currentTextChanged.connect(self._updateEstimate)
        form.addRow(t("Kare hızı (fps)"), self.fps)

        self.max_width = QSpinBox()
        self.max_width.setRange(0, 8192)
        self.max_width.setSingleStep(160)
        self.max_width.setSpecialValueText(t("Orijinal"))
        self.max_width.setSuffix(" px")
        form.addRow(t("Kareleri küçült"), self.max_width)
        layout.addLayout(form)

        self.estimate = QLabel("")
        self.estimate.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.estimate)

        layout.addStretch(1)
        self.busy = BusyBar()
        layout.addWidget(self.busy)

    # -- video seçimi ------------------------------------------------------

    def _browse(self) -> None:
        pattern = " ".join(f"*{suffix}" for suffix in VIDEO_SUFFIXES)
        path, _ = QFileDialog.getOpenFileName(
            self,
            t("Video seç"),
            "",
            t("Video dosyaları ({pattern});;Tüm dosyalar (*)", pattern=pattern),
        )
        if path:
            self._loadVideo(Path(path))

    def _loadVideo(self, path: Path) -> None:
        self.busy.start(t("{name} inceleniyor…", name=path.name))
        self.busy.cancel.setEnabled(False)

        def job(report, cancel):
            self.session.attach_video(path)
            return path

        worker = Worker(job, self)
        worker.succeeded.connect(lambda _: self._onVideoLoaded())
        worker.failed.connect(self._onError)
        worker.finished.connect(self.busy.stop)
        worker.start()
        self._worker = worker

    def _onVideoLoaded(self) -> None:
        self._extracted = False
        self.save()
        self._showSummary()
        self._updateEstimate()
        self.completeChanged.emit()

    def _onError(self, message: str) -> None:
        QMessageBox.critical(self, t("Video okunamadı"), message)

    def _showSummary(self) -> None:
        source = self.project.source
        if source is None:
            self.summary.setText(t("Henüz video seçilmedi."))
            return
        self.drop.setPrompt(Path(source.path).name)
        rotation = (
            t(" · {degrees}° döndürülmüş", degrees=source.rotation)
            if source.rotation
            else ""
        )
        audio = t("sesli") if source.has_audio else t("sessiz")
        self.summary.setText(
            t(
                "<b>{name}</b> — {seconds} sn · {fps} fps · {w}×{h}{rotation} · {audio}",
                name=Path(source.path).name,
                seconds=f"{source.duration_s:.2f}",
                fps=f"{source.native_fps:.3f}",
                w=source.display_width,
                h=source.display_height,
                rotation=rotation,
                audio=audio,
            )
        )

    # -- canlı hesap -------------------------------------------------------

    def _fpsValue(self) -> float:
        try:
            value = float(self.fps.currentText().replace(",", "."))
        except ValueError:
            return 0.0
        return value

    def _updateEstimate(self) -> None:
        source = self.project.source
        fps = self._fpsValue()
        if source is None or fps <= 0:
            self.estimate.setText("")
            self.completeChanged.emit()
            return

        frames = frame_extract.expected_frame_count(source.duration_s, fps)
        pages = layout_mod.page_count(frames, self.project.layout.cells_per_page)
        seconds = frames / max(fps, 1e-6)
        self.estimate.setText(
            t(
                "→ {frames} kare · ~{seconds} sn · {pages} sayfa {paper}",
                frames=frames,
                seconds=f"{seconds:.0f}",
                pages=pages,
                paper=self.project.layout.paper.value,
            )
        )
        self.completeChanged.emit()

    # -- sihirbaz kancaları -------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        if self.project.source is not None:
            self._showSummary()
            self.fps.setCurrentText(f"{self.project.extraction.target_fps:g}")
        self._updateEstimate()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return self.project.source is not None and self._fpsValue() > 0

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        """İleri denince kareleri çıkarır; bittiğinde sonraki adıma geçilir."""
        fps = self._fpsValue()
        frames_on_disk = frame_extract.list_frames(
            self.session.workspace.frames_dir, self.project.extraction.naming
        )
        unchanged = (
            self._extracted
            and abs(self.project.extraction.target_fps - fps) < 1e-9
            and len(frames_on_disk) == self.project.extraction.frame_count
        )
        if unchanged:
            return True

        self.project.extraction.target_fps = fps
        expected = frame_extract.expected_frame_count(self.project.source.duration_s, fps)
        max_width = self.max_width.value() or None

        def job(report, cancel):
            return frame_extract.extract_frames(
                self.project,
                self.session.workspace.root,
                max_width=max_width,
                progress=lambda update: report(update.frames_done, expected, ""),
                cancel=cancel,
            )

        self.busy.start(t("Kareler çıkarılıyor…"))
        worker = Worker(job, self)
        worker.progressed.connect(self.busy.update)
        worker.succeeded.connect(self._onExtracted)
        worker.failed.connect(self._onError)
        worker.cancelled.connect(self.busy.stop)
        worker.finished.connect(self.busy.stop)
        self.busy.cancelRequested.connect(worker.cancel)
        self._worker = worker
        worker.start()
        return False  # iş bitince sayfayı kendimiz ilerleteceğiz

    def _onExtracted(self, count: int) -> None:
        self.project.extraction.frame_count = count
        self.project.pages = layout_mod.build_pages(self.project)
        self.project.ingest = None  # kareler değişti, eski tarama artık geçersiz
        self.save()
        self._extracted = True
        self.wizard().next()
