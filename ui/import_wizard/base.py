"""IMPORT sihirbazının ortak parçaları (bkz. CLAUDE.md §8).

Sihirbaz dört adımdan oluşur ve her adım ayrı bir modüldedir:
``video_page`` → ``look_page`` → ``layout_page`` → ``output_page``.

Her adımda yapılan değişiklik projeye kaydedilir; uygulama kapansa bile iş
kaldığı yerden devam eder.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QWizardPage

log = logging.getLogger(__name__)

__all__ = ["WizardPage", "VIDEO_SUFFIXES", "PREVIEW_DEBOUNCE_MS", "open_in_file_manager"]

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".wmv")
# Kaydırıcı sürüklenirken saniyede onlarca istek gelir; bu kadar bekleyip
# yalnızca sonuncusunu işleriz.
PREVIEW_DEBOUNCE_MS = 150


def open_in_file_manager(path: Path) -> None:
    """Klasörü sistemin dosya yöneticisinde açar."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:  # pragma: no cover - dosya yöneticisi yoksa
        log.warning("Klasör açılamadı: %s", path)


class WizardPage(QWizardPage):
    """Sihirbazın paylaşılan durumuna kısa erişim."""

    @property
    def session(self):
        return self.wizard().session

    @property
    def project(self):
        return self.wizard().session.project

    def save(self) -> None:
        self.wizard().session.save()
