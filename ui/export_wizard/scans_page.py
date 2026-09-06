"""EXPORT adım 2: taramalar (bkz. CLAUDE.md §7.1–7.3, §8).

Her sayfa için tespit durumu rozetiyle gösterilir (yeşil/sarı/kırmızı).
Sayfa QR'ı okunamadıysa veya yanlış eşleştiyse kullanıcı sayfa numarasını
elle atayıp yeniden işletebilir.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...core import pipeline
from ...core.i18n import t
from ...core.constants import DEFAULT_BLEED_MM
from ...core.scan_ingest import IMAGE_SUFFIXES
from ..theme import STATUS_BAD, STATUS_GOOD, STATUS_WARN, mark_primary, status_pill
from ..widgets.common import BusyBar, DropArea, LabelledSlider
from .align_dialog import open_align_dialog
from ..workers import Worker

log = logging.getLogger(__name__)

__all__ = ["ScansPage"]

SCAN_SUFFIXES = tuple(sorted(IMAGE_SUFFIXES | {".pdf"}))
# Hizalama penceresi ham çözünürlük istemez; ekranda gösterilecek kadarı yeter.
_ALIGN_PREVIEW_DPI = 200

# Durum rozetleri: metin + renk. Renkler filmstrip ile aynı dili konuşur.
# Metinler Türkçe kaynak metindir; çeviri gösterim anında yapılır.
_BADGE = {
    "high": ("iyi", STATUS_GOOD),
    "medium": ("orta", STATUS_WARN),
    "low": ("zayıf", STATUS_WARN),
    "failed": ("okunamadı", STATUS_BAD),
}


class ScansPage(QWizardPage):
    """Taramaları toplar, işler ve sonucu rozetlerle gösterir."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Taramalar"))
        self.setSubTitle(
            t(
                "Taranan sayfaları ekleyin. Sıra önemsiz — "
                "sayfayı üzerindeki QR tanıtır."
            )
        )
        self._paths: list[Path] = []
        self._manual: dict[str, object] = {}
        self._worker: Worker | None = None
        self._processed = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.drop = DropArea(
            t("Taramaları buraya sürükleyin (görüntü, klasör veya çok sayfalı PDF)"),
            suffixes=SCAN_SUFFIXES,
            accept_dirs=True,
        )
        # Bu sayfada asıl iş listede; bırakma alanı yer kaplamasın.
        self.drop.setMinimumHeight(64)
        self.drop.setMaximumHeight(72)
        self.drop.dropped.connect(self._addPaths)
        layout.addWidget(self.drop)

        list_row = QHBoxLayout()
        self.files = QListWidget()
        self.files.setMaximumHeight(96)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        list_row.addWidget(self.files, 1)

        buttons = QVBoxLayout()
        add_files = QPushButton(t("Dosya ekle…"))
        add_files.clicked.connect(self._browseFiles)
        add_folder = QPushButton(t("Klasör ekle…"))
        add_folder.clicked.connect(self._browseFolder)
        remove = QPushButton(t("Çıkar"))
        remove.clicked.connect(self._removeSelected)
        for button in (add_files, add_folder, remove):
            buttons.addWidget(button)
        buttons.addStretch(1)
        list_row.addLayout(buttons)
        layout.addLayout(list_row)

        options = QFormLayout()
        self.bleed = LabelledSlider(-4, 6, DEFAULT_BLEED_MM, step=0.5, suffix=" mm", decimals=1)
        options.addRow(t("Kesim payı"), self.bleed)
        self.bleed_hint = QLabel(
            t("Negatif: kağıt kenarını alma. Pozitif: hücre dışına taşan çizimi de al.")
        )
        self.bleed_hint.setStyleSheet("color: palette(placeholderText);")
        self.bleed_hint.setWordWrap(True)
        options.addRow(self.bleed_hint)
        layout.addLayout(options)

        actions = QHBoxLayout()
        self.process = QPushButton(t("Taramaları işle"))
        self.process.setMinimumHeight(38)
        self.process.clicked.connect(self._process)
        mark_primary(self.process)
        actions.addWidget(self.process, 1)

        self.align = QPushButton(t("Seçili sayfayı elle hizala…"))
        self.align.setToolTip(
            t("İşaretler okunamadıysa sayfanın dört köşesini kendiniz gösterin.")
        )
        self.align.clicked.connect(self._alignSelected)
        self.align.setEnabled(False)
        actions.addWidget(self.align)
        layout.addLayout(actions)

        self.busy = BusyBar()
        self.busy.cancelRequested.connect(self._cancel)
        layout.addWidget(self.busy)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                t("Kaynak"),
                t("Durum"),
                t("Sayfa"),
                t("Yöntem"),
                t("İşaret"),
                t("Not"),
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self._onSelectionChanged)
        layout.addWidget(self.table, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    # -- dosya listesi -----------------------------------------------------

    def _addPaths(self, paths: list[Path]) -> None:
        for path in paths:
            if path not in self._paths:
                self._paths.append(path)
                self.files.addItem(str(path))
        self._processed = False
        self.completeChanged.emit()

    def _browseFiles(self) -> None:
        pattern = " ".join(f"*{suffix}" for suffix in SCAN_SUFFIXES)
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            t("Tarama seç"),
            "",
            t("Taramalar ({pattern});;Tüm dosyalar (*)", pattern=pattern),
        )
        self._addPaths([Path(p) for p in paths])

    def _browseFolder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, t("Tarama klasörü seç"))
        if path:
            self._addPaths([Path(path)])

    def _removeSelected(self) -> None:
        for item in self.files.selectedItems():
            path = Path(item.text())
            if path in self._paths:
                self._paths.remove(path)
            self.files.takeItem(self.files.row(item))
        self._processed = False
        self.completeChanged.emit()

    # -- işleme ------------------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        session = self.wizard().session
        if session is None:
            return
        if not self._paths and session.workspace.scans_dir.is_dir():
            existing = sorted(
                child
                for child in session.workspace.scans_dir.iterdir()
                if child.suffix.lower() in SCAN_SUFFIXES
            )
            if existing:
                self._addPaths([session.workspace.scans_dir])
        state = session.project.ingest
        if state is not None:
            self._manual.update(state.manual)
        if state is not None and state.cells:
            self.bleed.setValue(state.bleed_mm)
            self._showResults()
            self._processed = True
            self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        session = self.wizard().session
        if session is None:
            return False
        state = session.project.ingest
        return self._processed and state is not None and bool(state.cells)

    def _process(self) -> None:
        if not self._paths:
            QMessageBox.information(
                self, t("Tarama yok"), t("Önce tarama dosyası ekleyin.")
            )
            return

        session = self.wizard().session
        paths = list(self._paths)
        bleed = self.bleed.value()
        hints = self._collectHints()

        def job(report, cancel):
            return pipeline.run_ingest(
                session.workspace,
                session.project,
                paths,
                bleed_mm=bleed,
                page_hints=hints,
                manual=dict(self._manual),
                progress=lambda update: report(
                    update.scans_done, update.scans_total, ""
                ),
                cancel=cancel,
            )

        self.process.setEnabled(False)
        self.busy.start(t("Taramalar işleniyor…"))
        worker = Worker(job, self)
        worker.progressed.connect(self.busy.update)
        worker.succeeded.connect(self._onDone)
        worker.failed.connect(self._onFailed)
        worker.cancelled.connect(self._onFinished)
        worker.finished.connect(self._onFinished)
        self._worker = worker
        worker.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _onDone(self, _state) -> None:
        self._processed = True
        self._showResults()
        self.completeChanged.emit()

    def _onFailed(self, message: str) -> None:
        QMessageBox.critical(self, t("Taramalar işlenemedi"), message)

    def _onFinished(self) -> None:
        self.busy.stop()
        self.process.setEnabled(True)
        self._worker = None

    # -- sonuç tablosu -----------------------------------------------------

    def _onSelectionChanged(self) -> None:
        self.align.setEnabled(bool(self.table.selectedItems()))

    def _selectedSource(self) -> str | None:
        rows = {item.row() for item in self.table.selectedItems()}
        if len(rows) != 1:
            return None
        item = self.table.item(rows.pop(), 0)
        return item.text() if item is not None else None

    def _alignSelected(self) -> None:
        """Seçili tarama için elle hizalama penceresini açar (§7.4)."""
        source = self._selectedSource()
        if source is None:
            QMessageBox.information(
                self, t("Sayfa seçin"), t("Önce tablodan tek bir tarama seçin.")
            )
            return

        session = self.wizard().session
        scan = self._loadScan(source)
        if scan is None:
            QMessageBox.warning(
                self,
                t("Tarama bulunamadı"),
                t(
                    "'{source}' yeniden okunamadı. Dosya taşınmış olabilir.",
                    source=source,
                ),
            )
            return

        state = session.project.ingest
        existing = state.manual.get(source) if state else None
        result = open_align_dialog(
            source, scan.bgr, session.project, existing=existing, parent=self
        )
        if result is None:
            return

        alignment, apply_to_rest = result
        self._manual[source] = alignment
        if apply_to_rest:
            self._applyToFollowing(source, alignment)
        self._processed = False
        self.completeChanged.emit()
        QMessageBox.information(
            self,
            t("Hizalama kaydedildi"),
            t("Hizalamayı uygulamak için 'Taramaları işle' düğmesine basın."),
        )

    def _applyToFollowing(self, source: str, alignment) -> None:
        """Aynı hizalamayı sonraki taramalara da uygular (§7.4).

        Sayfa numarası taramaya özgüdür; onu taşımayız, yalnızca köşeleri ve
        ızgara kaydırmasını devrederiz.
        """
        names = [
            self.table.item(row, 0).text()
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None
        ]
        if source not in names:
            return
        for name in names[names.index(source) + 1 :]:
            self._manual[name] = alignment.model_copy(update={"page_no": None})

    def _loadScan(self, source: str):
        """Tablodaki kaynağı adından yeniden okur."""
        from ...core import scan_ingest

        for scan in scan_ingest.iter_scans(self._paths, render_dpi=_ALIGN_PREVIEW_DPI):
            if scan.name == source:
                return scan
        return None

    def _collectHints(self) -> dict[str, int]:
        """Kullanıcının elle atadığı sayfa numaralarını toplar."""
        hints: dict[str, int] = {}
        for row in range(self.table.rowCount()):
            source_item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 2)
            if source_item is None or combo is None:
                continue
            value = combo.currentData()
            if value is not None:
                hints[source_item.text()] = int(value)
        return hints

    def _showResults(self) -> None:
        session = self.wizard().session
        project = session.project
        state = project.ingest
        if state is None:
            return

        self.table.setRowCount(len(state.pages))
        page_numbers = [page.page_no for page in project.pages]

        for row, status in enumerate(state.pages):
            failed = status.method == "failed"

            source = QTableWidgetItem(status.source)
            self.table.setItem(row, 0, source)

            key = "failed" if failed else status.confidence
            label, colour = _BADGE.get(key, _BADGE["low"])
            badge = QLabel(status_pill(t(label), colour))
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setContentsMargins(6, 2, 6, 2)
            self.table.setCellWidget(row, 1, badge)

            combo = QComboBox()
            combo.addItem(t("otomatik"), None)
            for page_no in page_numbers:
                combo.addItem(str(page_no), page_no)
            if not failed:
                index = combo.findData(status.page_no)
                if index >= 0:
                    combo.setCurrentIndex(index)
            self.table.setCellWidget(row, 2, combo)

            self.table.setItem(row, 3, QTableWidgetItem(status.method))
            self.table.setItem(
                row,
                4,
                QTableWidgetItem(f"{status.corners_found}+{status.cell_markers_found}"),
            )
            note = QTableWidgetItem(" ".join(status.warnings))
            note.setToolTip("\n".join(status.warnings))
            self.table.setItem(row, 5, note)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )

        total = project.extraction.frame_count
        found = len(state.found_indices)
        missing = total - found
        scanned = {page.page_no for page in state.pages if page.method != "failed"}
        absent = [page.page_no for page in project.pages if page.page_no not in scanned]

        parts = [
            t("<b>{found}/{total}</b> kare çıkarıldı", found=found, total=total)
        ]
        if missing:
            parts.append(
                t(
                    "<span style='color:{colour}'>{missing} eksik</span>",
                    colour=STATUS_BAD,
                    missing=missing,
                )
            )
        if absent:
            parts.append(
                t(
                    "taranmamış sayfa: {pages}",
                    pages=", ".join(str(p) for p in absent),
                )
            )
        if any(page.method == "failed" for page in state.pages):
            parts.append(
                t(
                    "Okunamayan taramalar için sayfa numarasını elle seçip "
                    "yeniden işleyebilirsiniz."
                )
            )
        self.summary.setText(" · ".join(parts))

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        session = self.wizard().session
        if session is not None:
            session.save()
        return True
