"""EXPORT adım 3: kareler (bkz. CLAUDE.md §7.5–7.6, §8).

Filmstrip hangi karelerin bulunduğunu, hangilerinin eksik olduğunu ve
hangilerinin düşük güvenle eşleştiğini renk koduyla gösterir. Oynatma
önizlemesi, eksik kare politikası uygulanmış **gerçek** diziyi gösterir —
yani ekranda görülen ile dosyaya yazılacak olan aynıdır.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...core import sequencer
from ...core.errors import MixedMediaError
from ...core.i18n import t
from ...core.models import MissingFramePolicy
from ..theme import STATUS_BAD, STATUS_GOOD, STATUS_WARN
from ..widgets.common import ImageView, LabelledSlider, hline
from ..widgets.filmstrip import FilmstripView
from ..workers import numpy_to_qimage

log = logging.getLogger(__name__)

__all__ = ["FramesPage"]

_PREVIEW_SIZE = (480, 270)


class FramesPage(QWizardPage):
    """Filmstrip, eksik kare politikası, normalizasyon ve oynatma önizlemesi."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Kareler"))
        self.setSubTitle(
            t(
                "Bulunan kareleri gözden geçirin ve boşlukları nasıl "
                "dolduracağınızı seçin."
            )
        )
        self._slots: list[sequencer.SequenceSlot] = []
        self._position = 0

        layout = QVBoxLayout(self)

        self.filmstrip = FilmstripView()
        self.filmstrip.frameSelected.connect(self._onFrameSelected)
        layout.addWidget(self.filmstrip, 1)

        self.legend = QLabel(
            t(
                "<span style='color:{good}'>■</span> bulundu &nbsp;&nbsp; "
                "<span style='color:{warn}'>■</span> düşük güven &nbsp;&nbsp; "
                "<span style='color:{bad}'>■</span> eksik",
                good=STATUS_GOOD,
                warn=STATUS_WARN,
                bad=STATUS_BAD,
            )
        )
        layout.addWidget(self.legend)
        layout.addWidget(hline())

        bottom = QHBoxLayout()

        form = QFormLayout()
        self.policy = QComboBox()
        for policy, label in (
            (MissingFramePolicy.HOLD, "Öncekini uzat"),
            (MissingFramePolicy.SKIP, "Atla (video kısalır)"),
            (MissingFramePolicy.INTERPOLATE, "Ara geçiş üret"),
            (MissingFramePolicy.PLACEHOLDER, "Kırmızı kare (hata ayıklama)"),
        ):
            self.policy.addItem(t(label), policy)
        self.policy.currentIndexChanged.connect(self._onPolicyChanged)
        form.addRow(t("Eksik kareler"), self.policy)

        self.stabilize = LabelledSlider(0, 100, 60, suffix=" %")
        form.addRow(t("Stabilizasyon"), self.stabilize)

        self.stabilize_hint = QLabel(
            t(
                "Kağıdın tarayıcıdaki oynamasını siler, çizdiğiniz hareketi korur. "
                "Mixed media'da hafif titreşim genelde istenen bir estetiktir — "
                "<b>0</b> ham hâlini olduğu gibi bırakır. Kamerası çok hareketli "
                "bir kaynakta düşük bir değer daha güvenlidir."
            )
        )
        self.stabilize_hint.setWordWrap(True)
        self.stabilize_hint.setStyleSheet("color: palette(placeholderText);")
        # Açıklama iki sütuna yayılır; etiket sütununda girintili durması kötü okunuyor.
        form.addRow(self.stabilize_hint)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        form.addRow(t("Durum"), self.status)
        bottom.addLayout(form, 1)

        playback = QVBoxLayout()
        self.preview = ImageView()
        self.preview.setPlaceholder(t("Oynatma önizlemesi"))
        self.preview.setMinimumSize(*_PREVIEW_SIZE)
        playback.addWidget(self.preview, 1)

        controls = QHBoxLayout()
        self.play = QPushButton(t("Oynat"))
        self.play.setCheckable(True)
        self.play.toggled.connect(self._onPlayToggled)
        controls.addWidget(self.play)

        self.position_label = QLabel("")
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls.addWidget(self.position_label, 1)
        playback.addLayout(controls)
        bottom.addLayout(playback, 1)

        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

    # -- sihirbaz kancaları -------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        session = self.wizard().session
        project = session.project

        index = self.policy.findData(project.export.missing_frame_policy)
        if index >= 0:
            self.policy.setCurrentIndex(index)
        self.stabilize.setValue(project.export.stabilize * 100)

        self.filmstrip.populate(project, session.workspace.root)
        self._rebuild()

    def cleanupPage(self) -> None:  # noqa: N802 - Qt API
        self._stop()

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        self._stop()
        session = self.wizard().session
        session.project.export.missing_frame_policy = self.policy.currentData()
        session.project.export.stabilize = self.stabilize.value() / 100.0
        session.save()
        return bool(self._slots)

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return bool(self._slots)

    # -- dizi ---------------------------------------------------------------

    def _onPolicyChanged(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        """Seçilen politikayla çıktı dizisini kurar ve özeti günceller."""
        session = self.wizard().session
        project = session.project
        state = project.ingest
        if state is None:
            self._slots = []
            return

        available = {}
        for cell in state.cells:
            path = session.workspace.resolve(cell.file)
            if path.is_file():
                available[cell.frame_index] = path

        try:
            self._slots, report = sequencer.build_sequence(
                available,
                project.extraction.frame_count,
                self.policy.currentData(),
                low_confidence=state.low_confidence_indices,
            )
        except MixedMediaError as exc:
            self._slots = []
            self.status.setText(f"<span style='color:{STATUS_BAD}'>{exc.message}</span>")
            self.completeChanged.emit()
            return

        duration = report.output_length / max(project.export.output_fps, 1e-6)
        parts = [
            report.summary(),
            t(
                "{frames} kare yazılacak (~{seconds} sn)",
                frames=report.output_length,
                seconds=f"{duration:.1f}",
            ),
        ]
        if report.missing and self.policy.currentData() is MissingFramePolicy.SKIP:
            parts.append(t("Atlama seçildi: video kısalacak ve tempo değişecek."))
        self.status.setText("<br>".join(parts))

        self._position = min(self._position, max(0, len(self._slots) - 1))
        self._showCurrent()
        self.completeChanged.emit()

    def _onFrameSelected(self, frame_index: int) -> None:
        for position, slot in enumerate(self._slots):
            if slot.frame_index == frame_index:
                self._position = position
                break
        self._showCurrent()

    # -- oynatma ------------------------------------------------------------

    def _onPlayToggled(self, playing: bool) -> None:
        if playing and self._slots:
            fps = max(self.wizard().session.project.export.output_fps, 1.0)
            self._timer.start(int(1000 / fps))
            self.play.setText(t("Duraklat"))
        else:
            self._stop()

    def _stop(self) -> None:
        self._timer.stop()
        if self.play.isChecked():
            self.play.setChecked(False)
        self.play.setText(t("Oynat"))

    def _advance(self) -> None:
        if not self._slots:
            self._stop()
            return
        self._position = (self._position + 1) % len(self._slots)
        self._showCurrent()

    def _showCurrent(self) -> None:
        if not self._slots:
            self.preview.setPixmap(None)
            self.position_label.setText("")
            return

        slot = self._slots[self._position]
        try:
            image = sequencer.resolve_frame(
                slot, self._slots, self._position, _PREVIEW_SIZE
            )
        except MixedMediaError as exc:
            log.warning("Önizleme karesi okunamadı: %s", exc.message)
            self.preview.setPixmap(None)
            return

        self.preview.setPixmap(QPixmap.fromImage(numpy_to_qimage(image)))
        note = "" if slot.resolution == "found" else f"  ({slot.resolution})"
        self.position_label.setText(
            t(
                "{position}/{total} — kare {frame}{note}",
                position=self._position + 1,
                total=len(self._slots),
                frame=slot.frame_index,
                note=note,
            )
        )
