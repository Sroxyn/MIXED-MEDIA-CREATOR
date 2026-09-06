"""IMPORT adım 4: baskıya hazır belgeyi üret (bkz. CLAUDE.md §6.6, §8)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import pdf_writer
from ...core.i18n import t
from ...core.constants import PNG_EXPORT_DPI_CHOICES
from ..theme import mark_primary
from ..widgets.common import BusyBar
from ..workers import Worker
from .base import WizardPage, open_in_file_manager

log = logging.getLogger(__name__)

__all__ = ["OutputPage"]


class OutputPage(WizardPage):
    """PDF (veya sayfa PNG'leri) üretir, ilerlemeyi gösterir, klasörü açar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Çıktı"))
        self.setSubTitle(t("Baskıya hazır belgeyi üretin."))
        self._result: Path | None = None
        self._target: Path | None = None
        self._worker: Worker | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(10)

        self.kind = QComboBox()
        self.kind.addItem(t("PDF (tek belge)"), "pdf")
        self.kind.addItem(t("Sayfa başına PNG"), "png")
        self.kind.currentIndexChanged.connect(self._onKindChanged)
        form.addRow(t("Biçim"), self.kind)

        self.dpi = QComboBox()
        for choice in PNG_EXPORT_DPI_CHOICES:
            self.dpi.addItem(f"{choice} DPI", choice)
        form.addRow(t("Çözünürlük"), self.dpi)

        path_row = QHBoxLayout()
        self.path_label = QLabel("")
        self.path_label.setWordWrap(True)
        path_row.addWidget(self.path_label, 1)
        browse = QPushButton(t("Değiştir…"))
        browse.clicked.connect(self._browse)
        path_row.addWidget(browse)
        form.addRow(t("Hedef"), path_row)
        layout.addLayout(form)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.generate = QPushButton(t("Üret"))
        self.generate.setMinimumHeight(40)
        self.generate.clicked.connect(self._generate)
        mark_primary(self.generate)
        layout.addWidget(self.generate)

        self.busy = BusyBar()
        layout.addWidget(self.busy)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        self.open_folder = QPushButton(t("Klasörü aç"))
        self.open_folder.setVisible(False)
        self.open_folder.clicked.connect(self._openFolder)
        layout.addWidget(self.open_folder)
        layout.addStretch(1)

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        layout = self.project.layout
        self.summary.setText(
            t(
                "{pages} sayfa · {cells} kare · {paper} {orientation} @ {dpi} DPI",
                pages=len(self.project.pages),
                cells=self.project.total_cells,
                paper=layout.paper.value,
                orientation=t(layout.orientation.value),
                dpi=layout.dpi,
            )
        )
        self._onKindChanged()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return self._result is not None

    # -- hedef seçimi ------------------------------------------------------

    def _onKindChanged(self) -> None:
        is_png = self.kind.currentData() == "png"
        self.dpi.setEnabled(is_png)
        self._target = (
            self.session.workspace.out_dir / "pages"
            if is_png
            else self.session.default_pdf_path()
        )
        self.path_label.setText(str(self._target))

    def _browse(self) -> None:
        if self.kind.currentData() == "png":
            path = QFileDialog.getExistingDirectory(
                self, t("PNG klasörü seç"), str(self._target)
            )
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, t("PDF kaydet"), str(self._target), "PDF (*.pdf)"
            )
        if path:
            self._target = Path(path)
            self.path_label.setText(path)

    # -- üretim ------------------------------------------------------------

    def _generate(self) -> None:
        is_png = self.kind.currentData() == "png"
        target = self._target
        dpi = int(self.dpi.currentData())
        project = self.project
        root = self.session.workspace.root

        def job(report, cancel):
            def on_progress(update):
                report(update.page_done, update.page_total, "")

            if is_png:
                written = pdf_writer.write_page_pngs(
                    project, root, target, dpi, progress=on_progress, cancel=cancel
                )
                return written[0].parent if written else target
            return pdf_writer.write_pdf(
                project, root, target, progress=on_progress, cancel=cancel
            )

        self.generate.setEnabled(False)
        self.result_label.setText("")
        self.open_folder.setVisible(False)
        self.busy.start(t("Sayfalar işleniyor…"))

        worker = Worker(job, self)
        worker.progressed.connect(self.busy.update)
        worker.succeeded.connect(self._onDone)
        worker.failed.connect(self._onFailed)
        worker.finished.connect(self._onFinished)
        self.busy.cancelRequested.connect(worker.cancel)
        self._worker = worker
        worker.start()

    def _onDone(self, result: Path) -> None:
        self._result = Path(result)
        if self._result.is_file():
            size_mb = self._result.stat().st_size / (1024 * 1024)
            self.result_label.setText(
                t(
                    "✓ {path}   ({size} MB)",
                    path=self._result,
                    size=f"{size_mb:.1f}",
                )
            )
        else:
            self.result_label.setText(f"✓ {self._result}")
        self.open_folder.setVisible(True)
        self.completeChanged.emit()

    def _onFailed(self, message: str) -> None:
        QMessageBox.critical(self, t("Üretim başarısız"), message)

    def _onFinished(self) -> None:
        self.busy.stop()
        self.generate.setEnabled(True)
        self._worker = None

    def _openFolder(self) -> None:
        if self._result is None:
            return
        folder = self._result if self._result.is_dir() else self._result.parent
        open_in_file_manager(folder)
