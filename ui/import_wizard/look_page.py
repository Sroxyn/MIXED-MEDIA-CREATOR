"""IMPORT adım 2: baskı görünümü (bkz. CLAUDE.md §6.3, §8).

Mixed media'da sanatçı basılanın **üstüne** çizer; bu yüzden baskı açık tonlu
olmalıdır. Ayarlar tek bir kare üzerinde anlık olarak gösterilir.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...core.frame_process import process_frame_file
from ...core.i18n import t
from ...core.models import ProcessingMode
from ..widgets.common import ImageView, LabelledSlider, hline
from ..workers import Worker, pil_to_qpixmap
from .base import PREVIEW_DEBOUNCE_MS, WizardPage

log = logging.getLogger(__name__)

__all__ = ["LookPage"]


class LookPage(WizardPage):
    """Baskı işlemesini ayarlar; tek kare üzerinde anlık önizleme gösterir."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Görünüm"))
        self.setSubTitle(
            t("Sanatçı basılanın üstüne çizecek — baskıyı açık tonlu bırakmak işe yarar.")
        )
        self._worker: Worker | None = None
        self._pending = False

        layout = QHBoxLayout(self)
        controls = QVBoxLayout()
        form = QFormLayout()

        self.mode = QComboBox()
        for mode, label in (
            (ProcessingMode.ORIGINAL, "Orijinal (renkli)"),
            (ProcessingMode.GRAYSCALE, "Gri ton"),
            (ProcessingMode.LINEART, "Çizgi sanatı"),
            (ProcessingMode.HALFTONE, "Yarım ton"),
        ):
            self.mode.addItem(t(label), mode)
        self.mode.currentIndexChanged.connect(self._onChanged)
        form.addRow(t("İşleme modu"), self.mode)

        self.density = LabelledSlider(0, 100, 55, suffix=" %")
        self.density.valueChanged.connect(self._onChanged)
        form.addRow(t("Baskı yoğunluğu"), self.density)

        self.contrast = LabelledSlider(0.2, 2.5, 1.0, step=0.05, decimals=2)
        self.contrast.valueChanged.connect(self._onChanged)
        form.addRow(t("Kontrast"), self.contrast)

        self.brightness = LabelledSlider(-50, 50, 0, suffix=" %")
        self.brightness.valueChanged.connect(self._onChanged)
        form.addRow(t("Parlaklık"), self.brightness)

        self.gamma = LabelledSlider(0.3, 3.0, 1.0, step=0.05, decimals=2)
        self.gamma.valueChanged.connect(self._onChanged)
        form.addRow(t("Gama"), self.gamma)

        controls.addLayout(form)
        controls.addWidget(hline())
        controls.addWidget(QLabel(t("Önizlenen kare")))

        self.frame_index = LabelledSlider(0, 0, 0)
        self.frame_index.valueChanged.connect(lambda _: self._schedule())
        controls.addWidget(self.frame_index)

        self.hint = QLabel(
            t(
                "Yoğunluk düştükçe baskı açılır: üstüne çizmek kolaylaşır, "
                "ama hareketi takip etmek zorlaşır."
            )
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholderText);")
        controls.addWidget(self.hint)
        controls.addStretch(1)
        layout.addLayout(controls, 1)

        self.preview = ImageView()
        self.preview.setPlaceholder(t("Kare önizlemesi"))
        layout.addWidget(self.preview, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DEBOUNCE_MS)
        self._timer.timeout.connect(self._render)

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        processing = self.project.processing
        self.mode.setCurrentIndex(self.mode.findData(processing.mode))
        self.density.setValue(processing.print_density * 100)
        self.contrast.setValue(processing.contrast)
        self.brightness.setValue(processing.brightness * 100)
        self.gamma.setValue(processing.gamma)

        last = max(0, self.project.extraction.frame_count - 1)
        self.frame_index.slider.setMaximum(last)
        self.frame_index.setValue(min(self.frame_index.value(), last))
        self._schedule()

    def _onChanged(self, *_args) -> None:
        processing = self.project.processing
        processing.mode = self.mode.currentData()
        processing.print_density = self.density.value() / 100.0
        processing.contrast = self.contrast.value()
        processing.brightness = self.brightness.value() / 100.0
        processing.gamma = self.gamma.value()
        self._schedule()

    def _schedule(self) -> None:
        self._timer.start()

    def _render(self) -> None:
        """Önizlemeyi arka planda üretir; ana thread hiç bloke olmaz."""
        count = self.project.extraction.frame_count
        if count <= 0:
            self.preview.setPlaceholder(t("Önce kareleri çıkarın"))
            return
        if self._worker is not None and self._worker.isRunning():
            self._pending = True
            return

        index = min(int(self.frame_index.value()), count - 1)
        path = self.session.workspace.frames_dir / self.project.frame_filename(index)
        processing = self.project.processing.model_copy(deep=True)

        def job(report, cancel):
            return process_frame_file(path, processing)

        worker = Worker(job, self)
        worker.succeeded.connect(self._onRendered)
        worker.failed.connect(self._onFailed)
        worker.finished.connect(self._onFinished)
        self._worker = worker
        worker.start()

    def _onRendered(self, image) -> None:
        self.preview.setPixmap(pil_to_qpixmap(image))
        image.close()

    def _onFailed(self, message: str) -> None:
        log.warning("Kare önizlemesi çizilemedi: %s", message)
        self.preview.setPixmap(None)
        self.preview.setPlaceholder(message)

    def _onFinished(self) -> None:
        self._worker = None
        if self._pending:
            self._pending = False
            self._timer.start()

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        self.save()
        return True
