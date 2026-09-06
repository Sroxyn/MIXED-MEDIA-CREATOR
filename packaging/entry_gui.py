"""Paketlenmiş grafik arayüzün giriş noktası."""

from __future__ import annotations

import multiprocessing
import sys

from mixedmedia.ui.app import main

if __name__ == "__main__":
    # Paketlenmiş uygulamada alt süreçlerin uygulamayı yeniden başlatmasını
    # önler (PyInstaller'da klasik bir tuzak).
    multiprocessing.freeze_support()
    sys.exit(main())
