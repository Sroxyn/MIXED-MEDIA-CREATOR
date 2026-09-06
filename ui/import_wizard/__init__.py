"""IMPORT sihirbazı: video → kağıt (bkz. CLAUDE.md §8)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QWizard

from ...core.i18n import t
from ..session import Session
from .layout_page import LayoutPage
from .look_page import LookPage
from .output_page import OutputPage
from .video_page import VideoPage

__all__ = ["ImportWizard"]


class ImportWizard(QWizard):
    """Dört adımlı IMPORT sihirbazı.

    Her adımda geri dönülebilir ve canlı önizleme vardır. Tüm ayarlar
    ``project.mmp.json``'a yazılır; uygulama kapansa bile iş kaldığı yerden
    devam eder.
    """

    def __init__(self, session: Session, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session

        self.setWindowTitle(
            t("Baskıya hazırla — {name}", name=session.project.name)
        )
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.IndependentPages, False)
        self.setButtonText(QWizard.WizardButton.NextButton, t("İleri >"))
        self.setButtonText(QWizard.WizardButton.BackButton, t("< Geri"))
        self.setButtonText(QWizard.WizardButton.CancelButton, t("Kapat"))
        self.setButtonText(QWizard.WizardButton.FinishButton, t("Bitir"))
        self.resize(1080, 720)

        self.video_page = VideoPage()
        self.look_page = LookPage()
        self.layout_page = LayoutPage()
        self.output_page = OutputPage()
        for page in (self.video_page, self.look_page, self.layout_page, self.output_page):
            self.addPage(page)

    def done(self, result: int) -> None:  # noqa: N802 - Qt API
        """Kapanırken projeyi kaydeder — yarım kalan iş kaybolmaz."""
        self.session.save()
        super().done(result)
