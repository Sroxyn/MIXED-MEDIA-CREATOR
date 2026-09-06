"""Manuel hizalama penceresi (bkz. CLAUDE.md §7.4).

Bir sayfada yapılan düzeltme "sonraki sayfalara da uygula" ile tekrarlanabilir:
tarayıcıda kağıt genelde aynı yere konduğu için ilk sayfanın köşeleri
diğerlerine de yakın düşer ve kullanıcı her sayfayı baştan işaretlemek zorunda
kalmaz.
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import t
from ...core.layout import grid_from_settings
from ...core.models import ManualAlignment, Project
from ...core.rectify import paper_corner_guess
from ..widgets.common import LabelledSlider
from ..widgets.grid_editor import GridEditor

log = logging.getLogger(__name__)

__all__ = ["AlignDialog"]


class AlignDialog(QDialog):
    """Dört köşeyi sürükleyerek sayfayı elle hizalar."""

    def __init__(
        self,
        scan_name: str,
        bgr: np.ndarray,
        project: Project,
        *,
        existing: ManualAlignment | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("Elle hizala — {name}", name=scan_name))
        self.resize(900, 760)
        self._project = project
        # Kağıdın dış hatlarından bir başlangıç tahmini; kullanıcı zaten
        # sürükleyerek düzeltecek ama boş bir ekrandan başlamak zorunda kalmaz.
        self._guess = paper_corner_guess(bgr)

        layout = QVBoxLayout(self)

        hint = QLabel(
            t(
                "Sayfanın dört köşesini sürükleyerek gösterin. Izgara projedeki "
                "ölçülerden üstüne bindirilir; kağıda kaymış basıldıysa aşağıdaki "
                "kaydırma ile ince ayar yapın."
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholderText);")
        layout.addWidget(hint)

        self.editor = GridEditor()
        self.editor.setScan(bgr, grid_from_settings(project.layout))
        layout.addWidget(self.editor, 1)

        form = QFormLayout()

        self.page = QComboBox()
        self.page.addItem(t("otomatik (QR'dan)"), None)
        for page in project.pages:
            self.page.addItem(t("Sayfa {number}", number=page.page_no), page.page_no)
        form.addRow(t("Bu tarama"), self.page)

        self.offset_x = LabelledSlider(-15, 15, 0, step=0.5, suffix=" mm", decimals=1)
        self.offset_x.valueChanged.connect(self._onOffsetChanged)
        form.addRow(t("Izgarayı yatay kaydır"), self.offset_x)

        self.offset_y = LabelledSlider(-15, 15, 0, step=0.5, suffix=" mm", decimals=1)
        self.offset_y.valueChanged.connect(self._onOffsetChanged)
        form.addRow(t("Izgarayı dikey kaydır"), self.offset_y)

        self.apply_to_rest = QCheckBox(
            t("Bu hizalamayı sonraki taramalara da uygula")
        )
        self.apply_to_rest.setChecked(True)
        form.addRow(self.apply_to_rest)
        layout.addLayout(form)

        actions = QHBoxLayout()
        reset = QPushButton(t("Köşeleri tahmin et"))
        reset.clicked.connect(self._guessCorners)
        actions.addWidget(reset)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("Uygula"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("Vazgeç"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if existing is not None and len(existing.corners) == 4:
            self.editor.setCorners(existing.corners)
            self.offset_x.setValue(existing.grid_offset_mm[0])
            self.offset_y.setValue(existing.grid_offset_mm[1])
            index = self.page.findData(existing.page_no)
            if index >= 0:
                self.page.setCurrentIndex(index)
        else:
            self._guessCorners()

    # -- eylemler ----------------------------------------------------------

    def _guessCorners(self) -> None:
        """Köşeleri kağıt tahminine geri alır."""
        self.editor.setCorners(self._guess)

    def _onOffsetChanged(self, _value: float) -> None:
        self.editor.setGridOffset((self.offset_x.value(), self.offset_y.value()))

    # -- sonuç -------------------------------------------------------------

    def alignment(self) -> ManualAlignment:
        """Kullanıcının verdiği hizalamayı döner."""
        return ManualAlignment(
            corners=self.editor.corners(),
            page_no=self.page.currentData(),
            grid_offset_mm=(self.offset_x.value(), self.offset_y.value()),
        )

    def appliesToRest(self) -> bool:  # noqa: N802
        return self.apply_to_rest.isChecked()


def open_align_dialog(
    scan_name: str,
    bgr: np.ndarray,
    project: Project,
    *,
    existing: ManualAlignment | None = None,
    parent: QWidget | None = None,
) -> tuple[ManualAlignment, bool] | None:
    """Hizalama penceresini açar; kullanıcı onaylarsa ``(hizalama, tümü_mü)``."""
    dialog = AlignDialog(scan_name, bgr, project, existing=existing, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.alignment(), dialog.appliesToRest()
