"""EXPORT adım 4: videoyu dışa aktar (bkz. CLAUDE.md §7.7, §8)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...core import pipeline
from ...core.i18n import t
from ...core.models import Codec
from ..import_wizard.base import open_in_file_manager
from ..theme import STATUS_WARN, mark_primary
from ..widgets.common import BusyBar
from ..workers import Worker

log = logging.getLogger(__name__)

__all__ = ["VideoPage"]

_SUFFIX = {Codec.H264: ".mp4", Codec.PRORES: ".mov", Codec.PNG_SEQ: ""}
# Türkçe kaynak metin; çeviri ilerleme bildirimi anında yapılır.
_STAGE_LABELS = {
    "analyse": "Hareket çözümleniyor…",
    "frames": "Kareler hazırlanıyor…",
    "encode": "Kodlanıyor…",
}


class VideoPage(QWizardPage):
    """Kare hızı, çözünürlük, kodek ve sesi seçip videoyu üretir."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Video"))
        self.setSubTitle(t("Çıktı ayarlarını seçip dışa aktarın."))
        self._result: Path | None = None
        self._target: Path | None = None
        self._worker: Worker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(10)

        self.fps = QDoubleSpinBox()
        self.fps.setRange(0.1, 120.0)
        self.fps.setDecimals(2)
        self.fps.setSingleStep(1.0)
        self.fps.valueChanged.connect(self._updateSpeedNote)
        form.addRow(t("Kare hızı"), self.fps)

        self.speed_note = QLabel("")
        self.speed_note.setWordWrap(True)
        form.addRow(self.speed_note)

        size_row = QHBoxLayout()
        self.width = QSpinBox()
        self.width.setRange(16, 8192)
        self.height = QSpinBox()
        self.height.setRange(16, 8192)
        size_row.addWidget(self.width)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.height)
        size_row.addStretch(1)
        form.addRow(t("Çözünürlük"), size_row)

        self.codec = QComboBox()
        for codec, label in (
            (Codec.H264, "H.264 (teslim)"),
            (Codec.PRORES, "ProRes 422 HQ (montaja devam)"),
            (Codec.PNG_SEQ, "PNG sekansı"),
        ):
            self.codec.addItem(t(label), codec)
        self.codec.currentIndexChanged.connect(self._onCodecChanged)
        form.addRow(t("Kodek"), self.codec)

        self.audio = QCheckBox(t("Orijinal sesi yeniden ekle"))
        form.addRow(self.audio)

        path_row = QHBoxLayout()
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        path_row.addWidget(self.path_label, 1)
        browse = QPushButton(t("Değiştir…"))
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        form.addRow(t("Hedef"), path_row)
        layout.addLayout(form)

        self.export = QPushButton(t("Dışa aktar"))
        self.export.setMinimumHeight(40)
        self.export.clicked.connect(self._export)
        mark_primary(self.export)
        layout.addWidget(self.export)

        self.busy = BusyBar()
        self.busy.cancelRequested.connect(self._cancel)
        layout.addWidget(self.busy)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        self.open_folder = QPushButton(t("Klasörü aç"))
        self.open_folder.setVisible(False)
        self.open_folder.clicked.connect(self._openFolder)
        layout.addWidget(self.open_folder)
        layout.addStretch(1)

    # -- sihirbaz kancaları -------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        session = self.wizard().session
        project = session.project
        settings = project.export

        self.fps.setValue(settings.output_fps)
        width, height = settings.resolution or _source_size(project)
        self.width.setValue(width)
        self.height.setValue(height)

        index = self.codec.findData(settings.codec)
        if index >= 0:
            self.codec.setCurrentIndex(index)

        has_audio = bool(project.source and project.source.has_audio)
        self.audio.setEnabled(has_audio)
        self.audio.setChecked(settings.reattach_audio and has_audio)
        if not has_audio:
            self.audio.setText(t("Orijinal sesi yeniden ekle (kaynakta ses yok)"))

        self._onCodecChanged()
        self._updateSpeedNote()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return self._result is not None

    # -- ayarlar -----------------------------------------------------------

    def _onCodecChanged(self) -> None:
        session = self.wizard().session
        codec = self.codec.currentData()
        if codec is Codec.PNG_SEQ:
            self._target = session.workspace.out_dir / f"{session.slug}_final"
        else:
            self._target = session.default_video_path(_SUFFIX[codec])
        self.path_label.setText(str(self._target))
        self.audio.setEnabled(
            codec is not Codec.PNG_SEQ
            and bool(session.project.source and session.project.source.has_audio)
        )

    def _updateSpeedNote(self) -> None:
        """Çıkarma ve oynatma FPS'i farklıysa hız değişimini açıkça göster (§7.7)."""
        session = self.wizard().session
        if session is None:
            return
        source_fps = session.project.extraction.target_fps
        output_fps = self.fps.value()
        if abs(output_fps - source_fps) < 1e-6:
            self.speed_note.setText("")
            return
        factor = output_fps / source_fps
        direction = t("hızlanacak") if factor > 1 else t("yavaşlayacak")
        self.speed_note.setText(
            t(
                "<span style='color:{colour}'>{source} fps'te çıkarılan kareler "
                "{output} fps'te oynatılacak — hareket <b>{factor}×</b> "
                "{direction}.</span>",
                colour=STATUS_WARN,
                source=f"{source_fps:g}",
                output=f"{output_fps:g}",
                factor=f"{factor:.2g}",
                direction=direction,
            )
        )

    def _browse(self) -> None:
        codec = self.codec.currentData()
        if codec is Codec.PNG_SEQ:
            path = QFileDialog.getExistingDirectory(
                self, t("PNG klasörü seç"), str(self._target)
            )
        else:
            suffix = _SUFFIX[codec]
            path, _ = QFileDialog.getSaveFileName(
                self,
                t("Video kaydet"),
                str(self._target),
                t("Video (*{suffix})", suffix=suffix),
            )
        if path:
            self._target = Path(path)
            self.path_label.setText(path)

    # -- dışa aktarma ------------------------------------------------------

    def _export(self) -> None:
        session = self.wizard().session
        project = session.project
        project.export.output_fps = self.fps.value()
        project.export.codec = self.codec.currentData()
        project.export.resolution = (self.width.value(), self.height.value())
        project.export.reattach_audio = self.audio.isChecked()
        session.save()

        target = self._target

        def job(report, cancel):
            written, sequence_report = pipeline.run_export(
                session.workspace,
                project,
                target,
                progress=lambda update: report(
                    update.done,
                    update.total,
                    t(_STAGE_LABELS.get(update.stage, "İşleniyor…")),
                ),
                cancel=cancel,
            )
            return written, sequence_report

        self.export.setEnabled(False)
        self.result_label.setText("")
        self.open_folder.setVisible(False)
        self.busy.start(t("Hazırlanıyor…"))

        worker = Worker(job, self)
        worker.progressed.connect(self.busy.update)
        worker.succeeded.connect(self._onDone)
        worker.failed.connect(self._onFailed)
        worker.finished.connect(self._onFinished)
        self._worker = worker
        worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _onDone(self, result) -> None:
        written, report = result
        self._result = Path(written)
        lines = [f"✓ {self._result}"]
        if self._result.is_file():
            lines[0] += f"   ({self._result.stat().st_size / (1024 * 1024):.1f} MB)"
        lines.append(
            t(
                "{summary} · {frames} kare yazıldı",
                summary=report.summary(),
                frames=report.output_length,
            )
        )
        for note in report.notes:
            lines.append(
                t(
                    "<span style='color:{colour}'>Not: {note}</span>",
                    colour=STATUS_WARN,
                    note=note,
                )
            )
        self.result_label.setText("<br>".join(lines))
        self.open_folder.setVisible(True)
        self.completeChanged.emit()

    def _onFailed(self, message: str) -> None:
        QMessageBox.critical(self, t("Dışa aktarma başarısız"), message)

    def _onFinished(self) -> None:
        self.busy.stop()
        self.export.setEnabled(True)
        self._worker = None

    def _openFolder(self) -> None:
        if self._result is None:
            return
        folder = self._result if self._result.is_dir() else self._result.parent
        open_in_file_manager(folder)


def _source_size(project) -> tuple[int, int]:
    """Çözünürlük belirtilmediyse kaynak videonun görüntü boyutu."""
    if project.source is not None:
        return project.source.display_width, project.source.display_height
    return (1920, 1080)
