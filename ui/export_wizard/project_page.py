"""EXPORT adım 1: hangi proje (bkz. CLAUDE.md §8).

Export, taranan sayfalardan hiçbir şey tahmin etmez — her karenin sayfadaki
yerini ve sırasını ``project.mmp.json``'dan okur. Bu yüzden ilk iş, taramaları
üreten projeyi bulmaktır.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...core.constants import PROJECT_FILENAME
from ...core.errors import MixedMediaError
from ...core.i18n import t
from ...core.project import find_project_file
from ..session import Session
from ..theme import STATUS_WARN
from ..widgets.common import DropArea, hline

log = logging.getLogger(__name__)

__all__ = ["ProjectPage"]


class ProjectPage(QWizardPage):
    """Projeyi seçer veya hâlihazırda açık olanı doğrular."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle(t("Proje"))
        self.setSubTitle(
            t(
                "Taramaları üreten projeyi seçin — klasör ya da {filename}.",
                filename=PROJECT_FILENAME,
            )
        )

        layout = QVBoxLayout(self)
        self.drop = DropArea(
            t(
                "Proje klasörünü veya {filename} dosyasını buraya sürükleyin",
                filename=PROJECT_FILENAME,
            ),
            suffixes=(".json",),
            accept_dirs=True,
            on_browse=self._browse,
        )
        self.drop.dropped.connect(lambda paths: self._load(paths[0]))
        layout.addWidget(self.drop)

        layout.addWidget(hline())
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(self.summary.textFormat())
        layout.addWidget(self.summary)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {STATUS_WARN};")
        layout.addWidget(self.warning)
        layout.addStretch(1)

    # -- seçim -------------------------------------------------------------

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, t("Proje klasörü seç"))
        if path:
            self._load(Path(path))

    def _load(self, path: Path) -> None:
        try:
            find_project_file(path)
            session = Session.open(path)
        except MixedMediaError as exc:
            QMessageBox.critical(self, t("Proje açılamadı"), str(exc))
            return
        self.wizard().setSession(session)
        self._describe()
        self.completeChanged.emit()

    # -- sihirbaz kancaları -------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        self._describe()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        session = self.wizard().session
        return session is not None and bool(session.project.pages)

    def _describe(self) -> None:
        session = self.wizard().session
        if session is None:
            self.summary.setText(t("Henüz proje seçilmedi."))
            self.warning.setText("")
            return

        project = session.project
        self.drop.setPrompt(str(session.workspace.root))
        source = project.source
        video_line = (
            t(
                "{name} — {seconds} sn · {w}×{h}",
                name=Path(source.path).name,
                seconds=f"{source.duration_s:.2f}",
                w=source.display_width,
                h=source.display_height,
            )
            if source
            else t("kaynak video bağlı değil")
        )
        self.summary.setText(
            t(
                "<b>{name}</b>  ({identifier})<br>{video}<br>"
                "{frames} kare @ {fps} fps · {pages} sayfa {paper}",
                name=project.name,
                identifier=project.project_id,
                video=video_line,
                frames=project.extraction.frame_count,
                fps=f"{project.extraction.target_fps:g}",
                pages=len(project.pages),
                paper=project.layout.paper.value,
            )
        )

        notes = []
        if not project.pages:
            notes.append(
                t("Bu projede henüz sayfa yok. Önce IMPORT sihirbazını çalıştırın.")
            )
        if not project.layout.markers_enabled:
            notes.append(
                t(
                    "Bu proje işaretler kapalı basılmış: taramalar otomatik "
                    "hizalanamayacak."
                )
            )
        if project.ingest is not None and project.ingest.cells:
            found = len(project.ingest.found_indices)
            notes.append(
                t(
                    "Daha önce {found}/{total} kare alınmış — "
                    "yeni tarama eklerseniz baştan işlenir.",
                    found=found,
                    total=project.extraction.frame_count,
                )
            )
        self.warning.setText("<br>".join(notes))
