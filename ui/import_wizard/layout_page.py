"""IMPORT adım 3: sayfa düzeni (bkz. CLAUDE.md §6.4, §8).

Otomatik modda sistem, video en-boy oranına en uygun ``cols×rows``
kombinasyonunu basılan toplam görüntü alanını maksimize ederek seçer; hem
dikey hem yatay denenir. Manuel modda kullanıcı doğrudan girer. Her iki
durumda da önizleme anında güncellenir.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import layout as layout_mod
from ...core.constants import CELLS_PER_PAGE_PRESETS, MIN_EFFECTIVE_DPI
from ...core.errors import MixedMediaError
from ...core.i18n import t
from ...core.models import CutMarks, ImageFit, Orientation, PaperSize
from ..theme import STATUS_BAD, STATUS_WARN, mark_quiet
from ..widgets.common import LabelledSlider
from ..widgets.layout_preview import LayoutPreview
from .base import WizardPage

log = logging.getLogger(__name__)

__all__ = ["LayoutPage"]


class LayoutPage(WizardPage):
    """Izgarayı kurar ve sayfayı gerçek zamanlı gösterir."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Sayfa düzeni"))
        self.setSubTitle(t("Izgara değiştikçe önizleme anında güncellenir."))
        self._loading = False
        self._page_index = 0

        layout = QHBoxLayout(self)
        layout.setSpacing(22)

        controls = QWidget()
        controls.setLayout(self._buildControls())
        controls.setMinimumWidth(330)
        controls.setMaximumWidth(400)
        layout.addWidget(controls)
        layout.addLayout(self._buildPreview(), 1)

    # -- kurulum -----------------------------------------------------------

    def _buildControls(self) -> QVBoxLayout:
        controls = QVBoxLayout()
        controls.setSpacing(12)

        paper_form = QFormLayout()
        paper_form.setSpacing(8)
        self.paper = QComboBox()
        for size in PaperSize:
            self.paper.addItem(size.value, size)
        self.paper.currentIndexChanged.connect(self._apply)
        paper_form.addRow(t("Kağıt"), self.paper)

        self.orientation = QComboBox()
        for orient, label in (
            (Orientation.PORTRAIT, "Dikey"),
            (Orientation.LANDSCAPE, "Yatay"),
        ):
            self.orientation.addItem(t(label), orient)
        self.orientation.currentIndexChanged.connect(self._apply)
        paper_form.addRow(t("Yön"), self.orientation)
        controls.addLayout(paper_form)

        grid_box = QGroupBox(t("Izgara"))
        grid_layout = QVBoxLayout(grid_box)
        grid_layout.setContentsMargins(12, 14, 12, 12)
        grid_layout.setSpacing(8)

        self.auto_mode = QRadioButton(t("Otomatik — sayfa başına kare"))
        self.auto_mode.setChecked(True)
        self.auto_mode.toggled.connect(self._apply)
        grid_layout.addWidget(self.auto_mode)

        self.per_page = QComboBox()
        for preset in CELLS_PER_PAGE_PRESETS:
            self.per_page.addItem(str(preset), preset)
        self.per_page.setCurrentIndex(CELLS_PER_PAGE_PRESETS.index(6))
        self.per_page.currentIndexChanged.connect(self._apply)
        grid_layout.addWidget(self.per_page)

        self.manual_mode = QRadioButton(t("Manuel — sütun × satır"))
        self.manual_mode.toggled.connect(self._apply)
        grid_layout.addWidget(self.manual_mode)

        manual_row = QHBoxLayout()
        self.cols = QSpinBox()
        self.cols.setRange(1, 12)
        self.cols.setValue(2)
        self.cols.valueChanged.connect(self._apply)
        self.rows = QSpinBox()
        self.rows.setRange(1, 12)
        self.rows.setValue(3)
        self.rows.valueChanged.connect(self._apply)
        manual_row.addWidget(self.cols)
        manual_row.addWidget(QLabel("×"))
        manual_row.addWidget(self.rows)
        manual_row.addStretch(1)
        grid_layout.addLayout(manual_row)
        controls.addWidget(grid_box)

        spacing = QFormLayout()
        spacing.setSpacing(8)
        self.margin = LabelledSlider(0, 40, 12, suffix=" mm")
        self.margin.valueChanged.connect(self._apply)
        spacing.addRow(t("Kenar boşluğu"), self.margin)

        self.gutter = LabelledSlider(0, 40, 14, suffix=" mm")
        self.gutter.valueChanged.connect(self._apply)
        spacing.addRow(t("Hücre arası"), self.gutter)

        self.fit = QComboBox()
        for fit, label in ((ImageFit.CONTAIN, "Sığdır"), (ImageFit.COVER, "Doldur")):
            self.fit.addItem(t(label), fit)
        self.fit.currentIndexChanged.connect(self._apply)
        spacing.addRow(t("Görüntü"), self.fit)

        self.cut_marks = QComboBox()
        for marks, label in (
            (CutMarks.CORNER, "Köşe işaretleri"),
            (CutMarks.HAIRLINE, "İnce çerçeve"),
            (CutMarks.NONE, "Yok"),
        ):
            self.cut_marks.addItem(t(label), marks)
        self.cut_marks.currentIndexChanged.connect(self._apply)
        spacing.addRow(t("Kesim"), self.cut_marks)
        controls.addLayout(spacing)

        marks_box = QGroupBox(t("Sayfa üzerindeki işaretler"))
        marks_layout = QVBoxLayout(marks_box)
        marks_layout.setContentsMargins(12, 14, 12, 12)
        marks_layout.setSpacing(7)
        self.markers = QCheckBox(t("Köşe işaretleri ve sayfa QR'ı"))
        self.cell_markers = QCheckBox(t("Hücre başına mikro-marker"))
        self.calibration = QCheckBox(t("Gri skala kalibrasyon şeridi"))
        self.footer = QCheckBox(t("Alt bilgi metni"))
        for box in (self.markers, self.cell_markers, self.calibration, self.footer):
            box.setChecked(True)
            box.toggled.connect(self._apply)
            marks_layout.addWidget(box)
        self.marker_warning = QLabel("")
        self.marker_warning.setWordWrap(True)
        self.marker_warning.setStyleSheet(f"color: {STATUS_WARN};")
        marks_layout.addWidget(self.marker_warning)
        controls.addWidget(marks_box)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        controls.addWidget(self.info)
        controls.addStretch(1)
        return controls

    def _buildPreview(self) -> QVBoxLayout:
        right = QVBoxLayout()
        self.preview = LayoutPreview()
        right.addWidget(self.preview, 1)

        page_row = QHBoxLayout()
        self.prev_page = QPushButton("‹")
        self.prev_page.setFixedWidth(40)
        self.prev_page.clicked.connect(lambda: self._stepPage(-1))
        self.next_page = QPushButton("›")
        self.next_page.setFixedWidth(40)
        self.next_page.clicked.connect(lambda: self._stepPage(1))
        mark_quiet(self.prev_page, self.next_page)
        self.page_label = QLabel("")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page_row.addWidget(self.prev_page)
        page_row.addWidget(self.page_label, 1)
        page_row.addWidget(self.next_page)
        right.addLayout(page_row)
        return right

    # -- sihirbaz kancaları -------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        self._loading = True
        settings = self.project.layout
        self.paper.setCurrentIndex(self.paper.findData(settings.paper))
        self.orientation.setCurrentIndex(self.orientation.findData(settings.orientation))
        self.margin.setValue(settings.margin_mm)
        self.gutter.setValue(settings.gutter_mm)
        self.fit.setCurrentIndex(self.fit.findData(settings.image_fit))
        self.cut_marks.setCurrentIndex(self.cut_marks.findData(settings.cut_marks))
        self.markers.setChecked(settings.markers_enabled)
        self.cell_markers.setChecked(settings.cell_markers_enabled)
        self.calibration.setChecked(settings.calibration_strip)
        self.footer.setChecked(settings.footer_text)
        self.cols.setValue(settings.cols)
        self.rows.setValue(settings.rows)
        index = self.per_page.findData(settings.cells_per_page)
        if index >= 0:
            self.per_page.setCurrentIndex(index)
        # Kayıtlı ızgara, otomatik modun seçeceğinden farklıysa kullanıcı onu
        # elle kurmuş demektir; sihirbaza dönünce üzerine yazmayalım.
        if index < 0 or not self._matchesAutoChoice(settings):
            self.manual_mode.setChecked(True)
        else:
            self.auto_mode.setChecked(True)
        self._loading = False

        self.preview.setProject(self.project, self.session.workspace.root)
        self._apply()

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        self.save()
        return bool(self.project.pages)

    # -- iç işleyiş --------------------------------------------------------

    def _stepPage(self, delta: int) -> None:
        count = max(1, len(self.project.pages))
        self._page_index = (self._page_index + delta) % count
        self.preview.setPageIndex(self._page_index)
        self._updatePageLabel()

    def _updatePageLabel(self) -> None:
        count = len(self.project.pages)
        self.page_label.setText(
            t("Sayfa {current}/{total}", current=min(self._page_index + 1, count), total=count)
            if count
            else t("Sayfa yok")
        )
        self.prev_page.setEnabled(count > 1)
        self.next_page.setEnabled(count > 1)

    def _apply(self, *_args) -> None:
        """Denetimlerdeki değerleri projeye yazar ve önizlemeyi tazeler."""
        if self._loading:
            return

        settings = self.project.layout
        self.per_page.setEnabled(self.auto_mode.isChecked())
        self.cols.setEnabled(self.manual_mode.isChecked())
        self.rows.setEnabled(self.manual_mode.isChecked())
        self.cell_markers.setEnabled(self.markers.isChecked())

        settings.paper = self.paper.currentData()
        settings.orientation = self.orientation.currentData()
        settings.margin_mm = self.margin.value()
        settings.gutter_mm = self.gutter.value()
        settings.image_fit = self.fit.currentData()
        settings.cut_marks = self.cut_marks.currentData()
        settings.markers_enabled = self.markers.isChecked()
        settings.cell_markers_enabled = self.cell_markers.isChecked()
        settings.calibration_strip = self.calibration.isChecked()
        settings.footer_text = self.footer.isChecked()

        try:
            if self.auto_mode.isChecked():
                self._applyAutoGrid(settings)
            else:
                settings.cols = self.cols.value()
                settings.rows = self.rows.value()
            geometry = layout_mod.grid_from_settings(settings)
        except MixedMediaError as exc:
            self.info.setText(f"<span style='color:{STATUS_BAD}'>{exc.message}</span>")
            self.preview.view.setPixmap(None)
            self.preview.setPlaceholder(exc.message)
            return

        settings.cell_w_mm = round(geometry.cell_w_mm, 4)
        settings.cell_h_mm = round(geometry.cell_h_mm, 4)
        self.project.pages = layout_mod.build_pages(self.project)
        self._page_index = min(self._page_index, max(0, len(self.project.pages) - 1))

        self._updateInfo(geometry)
        self._updateWarnings()
        self._updatePageLabel()
        self.preview.setPageIndex(self._page_index)

    def _matchesAutoChoice(self, settings) -> bool:
        """Kayıtlı ızgara, otomatik modun aynı ayarlarla seçeceğiyle aynı mı?"""
        aspect = self.project.source.aspect_ratio if self.project.source else 1.0
        try:
            best = layout_mod.choose_best_grid_for_settings(
                settings, aspect, settings.cells_per_page
            )
        except MixedMediaError:
            return False
        return (best.cols, best.rows, best.orientation) == (
            settings.cols,
            settings.rows,
            settings.orientation,
        )

    def _applyAutoGrid(self, settings) -> None:
        """En çok basılı alanı veren ızgarayı seçer ve denetimlere yansıtır."""
        aspect = self.project.source.aspect_ratio if self.project.source else 1.0
        best = layout_mod.choose_best_grid_for_settings(
            settings, aspect, int(self.per_page.currentData())
        )
        settings.orientation = best.orientation
        settings.cols, settings.rows = best.cols, best.rows

        self._loading = True
        self.orientation.setCurrentIndex(self.orientation.findData(best.orientation))
        self.cols.setValue(best.cols)
        self.rows.setValue(best.rows)
        self._loading = False

    def _updateInfo(self, geometry) -> None:
        settings = self.project.layout
        aspect = self.project.source.aspect_ratio if self.project.source else 1.0
        box = layout_mod.fit_image_box(geometry.cell_at(0), aspect, settings.image_fit)

        lines = [
            t(
                "<b>{pages} sayfa</b> çıkacak · {cols}×{rows} ({per_page} kare/sayfa)",
                pages=len(self.project.pages),
                cols=settings.cols,
                rows=settings.rows,
                per_page=geometry.cells_per_page,
            ),
            t(
                "Hücre {cw}×{ch} mm · görüntü {iw}×{ih} mm",
                cw=f"{geometry.cell_w_mm:.1f}",
                ch=f"{geometry.cell_h_mm:.1f}",
                iw=f"{box.w_mm:.1f}",
                ih=f"{box.h_mm:.1f}",
            ),
        ]
        if self.project.source is not None:
            dpi = layout_mod.effective_dpi(self.project.source.display_width, box.w_mm)
            if dpi < MIN_EFFECTIVE_DPI:
                lines.append(
                    t(
                        "<span style='color:{colour}'>Efektif {dpi} DPI — "
                        "baskıda yumuşak görünecek.</span>",
                        colour=STATUS_WARN,
                        dpi=f"{dpi:.0f}",
                    )
                )
            else:
                lines.append(t("Efektif {dpi} DPI", dpi=f"{dpi:.0f}"))
        self.info.setText("<br>".join(lines))

    def _updateWarnings(self) -> None:
        if self.project.layout.markers_enabled:
            self.marker_warning.setText("")
        else:
            self.marker_warning.setText(
                t(
                    "İşaretler kapalı: taramadan geri okuma otomatik yapılamaz, "
                    "manuel hizalama gerekir."
                )
            )
