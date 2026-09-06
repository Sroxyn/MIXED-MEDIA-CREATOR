"""Sihirbazların paylaştığı küçük parçalar."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import t

__all__ = ["DropArea", "ImageView", "LabelledSlider", "BusyBar", "hline"]


def hline() -> QFrame:
    """İnce ayırıcı çizgi."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class DropArea(QFrame):
    """Sürükle-bırak alanı; tıklayınca da dosya seçtirir.

    Signals:
        dropped: Bırakılan (veya seçilen) yolların listesi.
    """

    dropped = Signal(list)

    def __init__(
        self,
        prompt: str,
        *,
        suffixes: Iterable[str] | None = None,
        accept_dirs: bool = False,
        on_browse: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._suffixes = {s.lower() for s in (suffixes or ())}
        self._accept_dirs = accept_dirs
        self._on_browse = on_browse

        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(96)
        self.setStyleSheet(
            "QFrame { border: 2px dashed palette(mid); border-radius: 8px; }"
            "QFrame[dragActive='true'] { border-color: palette(highlight); }"
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel(prompt)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("border: none; color: palette(placeholderText);")
        layout.addWidget(self.label)

        if on_browse is not None:
            browse = QPushButton(t("Gözat…"))
            browse.setStyleSheet("border: 1px solid palette(mid);")
            browse.clicked.connect(on_browse)
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(browse)
            row.addStretch(1)
            layout.addLayout(row)

    def setPrompt(self, text: str) -> None:
        self.label.setText(text)

    def _accepts(self, paths: Sequence[Path]) -> bool:
        for path in paths:
            if path.is_dir():
                if self._accept_dirs:
                    return True
                continue
            if not self._suffixes or path.suffix.lower() in self._suffixes:
                return True
        return False

    @staticmethod
    def _paths(event) -> list[Path]:
        return [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasUrls() and self._accepts(self._paths(event)):
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt API
        self.dragLeaveEvent(event)
        paths = [
            path
            for path in self._paths(event)
            if (path.is_dir() and self._accept_dirs)
            or (path.is_file() and (not self._suffixes or path.suffix.lower() in self._suffixes))
        ]
        if paths:
            self.dropped.emit(paths)
            event.acceptProposedAction()


class ImageView(QWidget):
    """Bir görüntüyü en-boy oranını koruyarak, ortalayarak gösterir."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._placeholder = t("Önizleme yok")
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def setPixmap(self, pixmap: QPixmap | None) -> None:  # noqa: N802 - Qt API benzeri
        self._pixmap = pixmap
        self.update()

    def setPlaceholder(self, text: str) -> None:  # noqa: N802 - Qt API benzeri
        self._placeholder = text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().alternateBase())

        if self._pixmap is None or self._pixmap.isNull():
            painter.setPen(self.palette().mid().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        scaled = self._pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)


class LabelledSlider(QWidget):
    """Değeri yanında yazan kaydırıcı; kesirli değerler için ölçekli.

    Signals:
        valueChanged: Yeni değer (gerçek birim, ölçek uygulanmış).
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        minimum: float,
        maximum: float,
        value: float,
        *,
        step: float = 1.0,
        suffix: str = "",
        decimals: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._step = step
        self._suffix = suffix
        self._decimals = decimals

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(int(round(minimum / step)))
        self.slider.setMaximum(int(round(maximum / step)))
        self.slider.setValue(int(round(value / step)))
        self.slider.valueChanged.connect(self._emit)
        layout.addWidget(self.slider, 1)

        self.readout = QLabel()
        self.readout.setMinimumWidth(64)
        self.readout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self.readout)
        self._updateReadout(self.value())

    def value(self) -> float:
        return self.slider.value() * self._step

    def setValue(self, value: float) -> None:  # noqa: N802 - Qt API benzeri
        self.slider.setValue(int(round(value / self._step)))

    def _emit(self) -> None:
        value = self.value()
        self._updateReadout(value)
        self.valueChanged.emit(value)

    def _updateReadout(self, value: float) -> None:
        self.readout.setText(f"{value:.{self._decimals}f}{self._suffix}")


class BusyBar(QWidget):
    """İlerleme çubuğu + iptal düğmesi. Gizliyken yer kaplamaz.

    Signals:
        cancelRequested: Kullanıcı iptal istedi.
    """

    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel("")
        layout.addWidget(self.label)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar, 1)

        self.cancel = QPushButton(t("İptal"))
        self.cancel.clicked.connect(self.cancelRequested)
        layout.addWidget(self.cancel)
        self.setVisible(False)

    def start(self, label: str) -> None:
        """Belirsiz süreli işi başlatır."""
        self.label.setText(label)
        self.bar.setRange(0, 0)
        self.cancel.setEnabled(True)
        self.setVisible(True)

    def update(self, done: int, total: int, label: str = "") -> None:
        if label:
            self.label.setText(label)
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
        else:
            self.bar.setRange(0, 0)

    def stop(self) -> None:
        self.setVisible(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
