"""Paketleme betiği: bir komutla teslim edilebilir klasör üretir.

    python packaging/build.py            # derle
    python packaging/build.py --clean    # önce eski çıktıyı sil

Sonuç ``dist/MixedMedia/`` altındadır ve olduğu gibi kopyalanabilir.
FFmpeg ayrı kurulur (bkz. mixedmedia.spec).
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

def _configure_console() -> None:
    """Çıktıyı UTF-8'e ayarlar.

    Windows konsolu varsayılan olarak cp1254 kullanır ve "✓" gibi karakterlerde
    çöker. Bu betik paketten bağımsız çalışabilsin diye korumayı burada
    tekrarlıyoruz (uygulamanın kendi kopyası ``mixedmedia/cli.py``'de).
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_configure_console()

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "mixedmedia.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def human_size(path: Path) -> str:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return f"{total / (1024 * 1024):.0f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MixedMedia'yı paketler.")
    parser.add_argument("--clean", action="store_true", help="Önce eski çıktıyı sil.")
    args = parser.parse_args(argv)

    if args.clean:
        for directory in (DIST, BUILD):
            shutil.rmtree(directory, ignore_errors=True)
        print(f"temizlendi: {DIST.name}/, {BUILD.name}/")

    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller kurulu değil:  pip install pyinstaller", file=sys.stderr)
        return 1

    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm",
         "--distpath", str(DIST), "--workpath", str(BUILD)],
        cwd=ROOT,
    )
    if result.returncode != 0:
        return result.returncode

    output = DIST / "MixedMedia"
    if not output.is_dir():
        print("Derleme bitti ama çıktı klasörü bulunamadı.", file=sys.stderr)
        return 1

    print()
    print(f"✓ {output}  ({human_size(output)})")
    print("  MixedMedia — grafik arayüz")
    print("  mm         — komut satırı")
    print()
    print("  FFmpeg ayrı gerekir: sisteme kurun ya da klasörü şuraya bırakın:")
    print(f"    {output / 'ffmpeg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
