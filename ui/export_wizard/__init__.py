"""EXPORT sihirbazı: kağıt → video (bkz. CLAUDE.md §8)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget, QWizard

from ...core.i18n import t
from ..session import Session
from .frames_page import FramesPage
from .project_page import ProjectPage
from .scans_page import ScansPage
from .video_page import VideoPage

__all__ = ["ExportWizard"]


class ExportWizard(QWizard):
    """Dört adımlı EXPORT sihirbazı.

    Proje ilk adımda seçilebilir; ana pencereden açık bir projeyle de
    başlatılabilir. Tüm ayarlar ``project.mmp.json``'a yazılır.
    """

    def __init__(self, session: Session | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.session = session

        title = session.project.name if session else t("proje seçin")
        self.setWindowTitle(t("Videoya dönüştür — {name}", name=title))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setButtonText(QWizard.WizardButton.NextButton, t("İleri >"))
        self.setButtonText(QWizard.WizardButton.BackButton, t("< Geri"))
        self.setButtonText(QWizard.WizardButton.CancelButton, t("Kapat"))
        self.setButtonText(QWizard.WizardButton.FinishButton, t("Bitir"))
        self.resize(1080, 760)

        self.project_page = ProjectPage()
        self.scans_page = ScansPage()
        self.frames_page = FramesPage()
        self.video_page = VideoPage()
        for page in (self.project_page, self.scans_page, self.frames_page, self.video_page):
            self.addPage(page)

    def setSession(self, session: Session) -> None:  # noqa: N802 - Qt API benzeri
        """Açık projeyi değiştirir (ilk adımda proje seçilince çağrılır)."""
        self.session = session
        self.setWindowTitle(
            t("Videoya dönüştür — {name}", name=session.project.name)
        )

    def done(self, result: int) -> None:  # noqa: N802 - Qt API
        if self.session is not None:
            self.session.save()
        super().done(result)
