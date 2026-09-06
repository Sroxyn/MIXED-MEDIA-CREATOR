"""Paketlenmiş komut satırı aracının giriş noktası."""

from __future__ import annotations

import multiprocessing

from mixedmedia.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
