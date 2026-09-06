# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller tarifi — Windows ve macOS (bkz. CLAUDE.md §11, M7).

Tek bir ``dist/MixedMedia`` klasörü üretir; içinde iki çalıştırılabilir vardır:

* ``MixedMedia`` — grafik arayüz (penceresiz, konsol açmaz)
* ``mm``         — komut satırı aracı (konsollu)

İkisi de aynı kütüphaneleri paylaşır, bu yüzden tek bir ``COLLECT`` yeterlidir.

Kurulum::

    pip install pyinstaller
    pyinstaller packaging/mixedmedia.spec --noconfirm

**FFmpeg pakete gömülmez.** Lisansı dağıtımda ek yükümlülük getirir ve
derlemeyi onlarca megabayt şişirir. Uygulama onu sırayla şuralarda arar:
``MM_FFMPEG_DIR`` → uygulamanın yanındaki ``ffmpeg/bin`` → ``ffmpeg`` → PATH.
Yani kullanıcı ya sisteme kurar ya da klasörü uygulamanın yanına bırakır.

**Yazı tipi de gömülmez.** Sistem yazı tipleri (Segoe UI, Helvetica) lisansları
gereği dağıtılamaz; uygulama çalışma anında sistemden Unicode kapsayan birini
bulur. Kullanıcı isterse uygulamanın yanındaki ``fonts/`` klasörüne kendi
``.ttf``'sini bırakabilir — o sistemin önüne geçer.
"""

import pathlib
import sys
from pathlib import Path

APP_NAME = "MixedMedia"
ROOT = Path(SPECPATH).resolve().parent

# Simge derleme anında çizilir, depoda ikili dosya taşımayız. Aynı çizim
# uygulamanın pencere simgesini de üretir (bkz. mixedmedia/core/logo.py), yani
# görev çubuğundaki ile .exe'nin üstündeki simgenin ayrı düşmesi mümkün değil.
sys.path.insert(0, str(ROOT))
from mixedmedia.core.logo import write_icon, write_png  # noqa: E402

BUILD_DIR = ROOT / "build" / "branding"
ICON_PATH = write_icon(BUILD_DIR / f"{APP_NAME}.ico")
# macOS .icns ister; PyInstaller PNG'yi kendisi dönüştürür.
MAC_ICON_PATH = write_png(BUILD_DIR / f"{APP_NAME}.png", 1024)

# Qt'nin kullanmadığımız modülleri derlemeyi yüz megabaytlarca şişirir.
_UNUSED_QT = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNetworkAuth",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

EXCLUDES = _UNUSED_QT + [
    "pytest", "_pytest", "pyflakes", "setuptools", "pip",
    "tkinter", "matplotlib", "IPython", "notebook", "pandas", "scipy",
]

# Bu paketler her şeyi statik çözümlemeyle bulunamayan alt modüller taşır.
HIDDEN = [
    "mixedmedia.core.example",
    "mixedmedia.ui.app",
    "PIL._tkinter_finder",
]


# Bu ikililer bağımlılıklarla birlikte gelir ama bu uygulama onları hiç
# kullanmaz. Bırakılırlarsa derleme ~70 MB şişer.
_DROP_BINARIES = (
    # QtWidgets uygulamasıyız: QML/Quick yığınının tamamı ölü ağırlık.
    "qt6qml", "qt6quick", "qt6qmlmeta", "qt6qmlmodels", "qt6qmlworkerscript",
    "qt6virtualkeyboard", "qt6pdf", "qt6opengl", "qt6network",
    # Yazılım OpenGL yedeği: düz widget arayüzünde kullanılmıyor (~20 MB).
    "opengl32sw",
    # Pillow'un AVIF eklentisi: bu biçimi hiç açmıyoruz (~7 MB).
    "_avif",
)


def _keep(entry) -> bool:
    """Kullanılmayan büyük ikilileri derlemenin dışında bırakır."""
    name = pathlib.PurePath(entry[0]).name.lower()
    return not any(name.startswith(prefix) for prefix in _DROP_BINARIES)


def _analysis(entry: str) -> Analysis:  # noqa: F821 - PyInstaller enjekte eder
    return Analysis(
        [str(ROOT / entry)],
        pathex=[str(ROOT)],
        binaries=[],
        datas=[],
        hiddenimports=HIDDEN,
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=False,
    )


gui_analysis = _analysis("packaging/entry_gui.py")
cli_analysis = _analysis("packaging/entry_cli.py")

for _analysed in (gui_analysis, cli_analysis):
    _analysed.binaries = TOC(b for b in _analysed.binaries if _keep(b))  # noqa: F821

MERGE((gui_analysis, "gui", "gui"), (cli_analysis, "cli", "cli"))  # noqa: F821

gui_pyz = PYZ(gui_analysis.pure)  # noqa: F821
cli_pyz = PYZ(cli_analysis.pure)  # noqa: F821

gui_exe = EXE(  # noqa: F821
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    strip=False,
    upx=False,
    # Penceresiz: arkasında siyah bir konsol açılmasın.
    console=False,
    disable_windowed_traceback=False,
    icon=str(ICON_PATH),
)

cli_exe = EXE(  # noqa: F821
    cli_pyz,
    cli_analysis.scripts,
    [],
    exclude_binaries=True,
    name="mm",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(ICON_PATH),
)

collect = COLLECT(  # noqa: F821
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        collect,
        name=f"{APP_NAME}.app",
        icon=str(MAC_ICON_PATH),
        bundle_identifier="studio.mixedmedia.roundtrip",
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "MixedMedia Round-Trip Studio",
            "NSHighResolutionCapable": True,
            # Kullanıcı videolarını ve taramalarını seçebilsin.
            "NSDesktopFolderUsageDescription": "Proje klasörlerinize erişmek için.",
            "NSDocumentsFolderUsageDescription": "Proje klasörlerinize erişmek için.",
        },
    )
