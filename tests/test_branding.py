"""Logo ve uygulama simgesi (bkz. mixedmedia/core/logo.py).

Bir logonun "güzel" olup olmadığı sınanamaz, ama şunlar sınanabilir: her boyutta
üretilebiliyor mu, küçük boyutta tek renge çökmüş mü (yani okunaksız mı), ve
paketleme tarifi onu gerçekten kullanıyor mu.
"""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image

from mixedmedia.core import logo

SPEC = pathlib.Path(__file__).resolve().parents[1] / "packaging" / "mixedmedia.spec"


# ---------------------------------------------------------------------------
# Çizim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [16, 32, 64, 256, 512])
def test_logo_renders_at_every_size(size):
    image = logo.render_logo(size)
    assert image.size == (size, size)
    assert image.mode == "RGBA"


def test_badge_fills_the_frame():
    """Rozet köşeleri yuvarlak ama ortası dolu olmalı."""
    image = logo.render_logo(64)
    assert image.getpixel((32, 32))[3] == 255  # merkez opak
    assert image.getpixel((0, 0))[3] < 255  # köşe yuvarlatılmış


def test_logo_without_badge_is_transparent_at_the_corners():
    image = logo.render_logo(64, badge=False)
    assert image.getpixel((1, 1))[3] == 0


@pytest.mark.parametrize("size", [16, 24, 32, 48])
def test_small_sizes_keep_their_contrast(size):
    """Küçültünce tek bir lekeye dönüşmemeli; yoksa görev çubuğunda okunmaz."""
    grey = logo.render_logo(size).convert("L")
    values = list(grey.get_flattened_data())
    assert max(values) - min(values) > 120, "simge küçük boyutta kontrastını yitiriyor"


def test_the_three_elements_are_distinguishable():
    """Zemin, ok ve kağıt birbirinden ayrı renklerde olmalı."""
    image = logo.render_logo(256).convert("RGB")
    # Yeterince farklı renk kalmalı: tek düze bir kare değil.
    assert len(set(image.get_flattened_data())) > 200


# ---------------------------------------------------------------------------
# Dosya çıktısı
# ---------------------------------------------------------------------------


def test_icon_carries_every_size(tmp_path):
    path = logo.write_icon(tmp_path / "app.ico")
    assert path.is_file()
    with Image.open(path) as handle:
        stored = {size for size in handle.info["sizes"]}
    assert stored == {(s, s) for s in logo.ICON_SIZES}


def test_png_is_written_at_the_requested_size(tmp_path):
    path = logo.write_png(tmp_path / "app.png", 128)
    with Image.open(path) as handle:
        assert handle.size == (128, 128)


def test_write_creates_missing_directories(tmp_path):
    path = logo.write_png(tmp_path / "yeni" / "klasor" / "app.png", 32)
    assert path.is_file()


# ---------------------------------------------------------------------------
# Paketleme
# ---------------------------------------------------------------------------


def test_spec_uses_the_generated_icon():
    """Simge derleme anında çizilmeli — depoda ikili dosya tutmuyoruz."""
    text = SPEC.read_text(encoding="utf-8")
    assert "write_icon" in text
    assert "icon=str(ICON_PATH)" in text
    assert "icon=None" not in text


def test_spec_gives_the_mac_bundle_an_icon():
    text = SPEC.read_text(encoding="utf-8")
    assert "icon=str(MAC_ICON_PATH)" in text


# ---------------------------------------------------------------------------
# Qt köprüsü
# ---------------------------------------------------------------------------


def test_app_icon_offers_the_small_sizes(qapp):
    from mixedmedia.ui.branding import app_icon

    icon = app_icon()
    assert not icon.isNull()
    available = {size.width() for size in icon.availableSizes()}
    assert {16, 32, 256} <= available


def test_logo_pixmap_scales(qapp):
    from mixedmedia.ui.branding import logo_pixmap

    assert logo_pixmap(48).size().width() == 48


# ---------------------------------------------------------------------------
# Dil ekrani
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_language(tmp_path, monkeypatch):
    """Testler kullanicinin gercek dil tercihine dokunmasin."""
    from mixedmedia.core import i18n

    monkeypatch.setenv("MM_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(i18n, "_loaded", False)
    monkeypatch.setattr(i18n, "_current", "tr")
    yield i18n
    i18n._loaded = False
    i18n._current = "tr"


def test_language_dialog_lists_every_language(qapp, clean_language):
    from mixedmedia.ui.language import LanguageDialog

    dialog = LanguageDialog()
    try:
        from PySide6.QtCore import Qt

        listed = {
            dialog.list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(dialog.list.count())
        }
        assert listed == set(clean_language.available_languages())
        assert dialog.selectedLanguage() == "tr"
    finally:
        dialog.deleteLater()


def test_language_dialog_preselects_the_current_language(qapp, clean_language):
    from mixedmedia.ui.language import LanguageDialog

    clean_language.set_language("en", persist=False)
    dialog = LanguageDialog()
    try:
        assert dialog.selectedLanguage() == "en"
    finally:
        dialog.deleteLater()


def test_main_window_retranslates_live(qapp, clean_language):
    from mixedmedia.ui.app import MainWindow

    window = MainWindow()
    try:
        assert window.new_button.text() == "Yeni proje…"
        clean_language.set_language("en", persist=False)
        assert window.new_button.text() == "New project…"
        assert "English" in window.language_button.text()
    finally:
        window.close()
        window.deleteLater()


def test_main_window_stops_listening_after_close(qapp, clean_language):
    """Kapanan pencere dil degisiminde yeniden cizilmeye calismamali."""
    from mixedmedia.ui.app import MainWindow

    window = MainWindow()
    window.close()
    before = len(clean_language._listeners)
    window.deleteLater()
    assert before == 0


def test_window_carries_the_icon(qapp, clean_language):
    from mixedmedia.ui.app import MainWindow

    window = MainWindow()
    try:
        assert not window.windowIcon().isNull()
    finally:
        window.close()
        window.deleteLater()
