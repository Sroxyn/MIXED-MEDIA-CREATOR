"""Çeviri katmanı (bkz. mixedmedia/core/i18n.py).

Buradaki asıl iş iki denetim:

* Kaynakta geçen her ``t("…")`` anahtarının İngilizce kataloğunda karşılığı var
  mı — yoksa arayüz yarı Türkçe kalır ve bunu elle fark etmek zordur.
* Çeviriler kaynakla **aynı** yer tutucuları taşıyor mu — eksik bir yer tutucu
  ekrana yanlış bilgi, fazlası ise ``KeyError`` demektir.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mixedmedia.core import i18n, settings
from mixedmedia.core.constants import DEFAULT_LANGUAGE
from mixedmedia.core.i18n import t
from mixedmedia.core.locales import CATALOGUES, LANGUAGE_NAMES

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "mixedmedia"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _literal_keys() -> list[tuple[str, pathlib.Path]]:
    """Kaynaktaki ``t("sabit")`` çağrılarının anahtarlarını toplar."""
    found: list[tuple[str, pathlib.Path]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "t":
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((first.value, path))
    return found


def _placeholders(text: str) -> set[str]:
    """Metindeki ``{ad}`` yer tutucularının adları."""
    from string import Formatter

    return {
        field
        for _, field, _, _ in Formatter().parse(text)
        if field is not None and field != ""
    }


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Testler kullanıcının gerçek ayar dosyasına dokunmasın."""
    monkeypatch.setenv("MM_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(i18n, "_loaded", False)
    monkeypatch.setattr(i18n, "_current", DEFAULT_LANGUAGE)
    yield
    i18n._loaded = False
    i18n._current = DEFAULT_LANGUAGE


# ---------------------------------------------------------------------------
# Katalog eksiksizliği
# ---------------------------------------------------------------------------


def test_source_uses_some_translation_calls():
    """Tarayıcı gerçekten bir şey buluyor mu — yoksa aşağıdaki testler boşa geçer."""
    assert len(_literal_keys()) > 100


@pytest.mark.parametrize("language", sorted(CATALOGUES))
def test_every_key_is_translated(language):
    catalogue = CATALOGUES[language]
    missing = sorted({key for key, _ in _literal_keys() if key not in catalogue})
    assert not missing, f"{language} kataloğunda eksik: {missing[:5]}"


@pytest.mark.parametrize("language", sorted(CATALOGUES))
def test_placeholders_match_the_source(language):
    """Çeviri, kaynakla aynı yer tutucuları taşımalı."""
    problems = []
    for source, translated in CATALOGUES[language].items():
        if _placeholders(source) != _placeholders(translated):
            problems.append(source[:60])
    assert not problems, f"{language}: yer tutucu uyuşmazlığı {problems}"


@pytest.mark.parametrize("language", sorted(CATALOGUES))
def test_catalogue_has_no_stale_entries(language):
    """Kaynakta artık geçmeyen anahtar kalmasın — katalog şişmesin.

    Dolaylı anahtarlar (açılır liste etiketleri, rozetler) sabit olarak
    yazılmadığı için ayrıca listelenir.
    """
    used = {key for key, _ in _literal_keys()}
    indirect = _indirect_keys()
    stale = sorted(set(CATALOGUES[language]) - used - indirect)
    assert not stale, f"{language} kataloğunda kullanılmayan anahtar: {stale}"


def _indirect_keys() -> set[str]:
    """``t(değişken)`` biçiminde geçen, kaynakta sabit olmayan anahtarlar."""
    from mixedmedia.core.models import (
        Codec,
        CutMarks,
        ImageFit,
        MissingFramePolicy,
        Orientation,
        ProcessingMode,
    )
    from mixedmedia.ui.export_wizard.scans_page import _BADGE
    from mixedmedia.ui.export_wizard.video_page import _STAGE_LABELS
    from mixedmedia.ui.widgets.filmstrip import _STATUS_LABEL
    from mixedmedia.ui.widgets.grid_editor import _CORNER_NAMES

    keys = set(_CORNER_NAMES)
    keys |= set(_STATUS_LABEL.values())
    keys |= {label for label, _colour in _BADGE.values()}
    keys |= set(_STAGE_LABELS.values())
    keys |= {"İşleniyor…"}
    # OperationCancelled'a çağrı yerlerinden geçen iş adları.
    keys |= {
        "İşlem",
        "Tarama alma",
        "Video üretimi",
        "Kare çıkarma",
        "PDF üretimi",
        "PNG üretimi",
    }
    # Enum değerleri ekranda göründüğü yerlerde t()'den geçer.
    keys |= {member.value for member in Orientation}
    # Açılır liste etiketleri kaynakta demet içinde durur, t(label) ile çevrilir.
    keys |= {
        "Orijinal (renkli)",
        "Gri ton",
        "Çizgi sanatı",
        "Yarım ton",
        "Dikey",
        "Yatay",
        "Sığdır",
        "Doldur",
        "Köşe işaretleri",
        "İnce çerçeve",
        "Yok",
        "Öncekini uzat",
        "Atla (video kısalır)",
        "Ara geçiş üret",
        "Kırmızı kare (hata ayıklama)",
        "H.264 (teslim)",
        "ProRes 422 HQ (montaja devam)",
        "PNG sekansı",
    }
    # Bu enum'lar yalnızca yukarıdaki etiketlerin anahtarı; değerleri
    # ekrana çıkmaz ama içe aktarımın kırılmadığını burada doğruluyoruz.
    assert Codec and CutMarks and ImageFit and MissingFramePolicy and ProcessingMode
    return keys


# ---------------------------------------------------------------------------
# Motor davranışı
# ---------------------------------------------------------------------------


def test_default_language_is_turkish():
    assert i18n.current_language() == "tr"
    assert DEFAULT_LANGUAGE == "tr"


def test_turkish_returns_the_key_itself():
    assert t("Yeni proje…") == "Yeni proje…"


def test_switching_language_translates():
    i18n.set_language("en", persist=False)
    assert t("Yeni proje…") == "New project…"


def test_unknown_key_falls_back_to_the_source_text():
    i18n.set_language("en", persist=False)
    assert t("Böyle bir metin katalogda yok") == "Böyle bir metin katalogda yok"


def test_unknown_language_is_ignored():
    i18n.set_language("kl", persist=False)
    assert i18n.current_language() == "tr"


def test_placeholders_are_filled():
    assert t("Sayfa {number}", number=3) == "Sayfa 3"
    i18n.set_language("en", persist=False)
    assert t("Sayfa {number}", number=3) == "Page 3"


def test_available_languages_start_with_the_default():
    codes = i18n.available_languages()
    assert codes[0] == DEFAULT_LANGUAGE
    assert set(codes) == set(LANGUAGE_NAMES)


def test_language_names_are_in_their_own_language():
    assert i18n.language_name("tr") == "Türkçe"
    assert i18n.language_name("en") == "English"


def test_listeners_are_notified():
    seen: list[str] = []
    i18n.subscribe(seen.append)
    try:
        i18n.set_language("en", persist=False)
    finally:
        i18n.unsubscribe(seen.append)
    assert seen == ["en"]


def test_a_broken_listener_does_not_break_the_switch():
    def explode(_code):
        raise RuntimeError("test")

    i18n.subscribe(explode)
    try:
        i18n.set_language("en", persist=False)
    finally:
        i18n.unsubscribe(explode)
    assert i18n.current_language() == "en"


# ---------------------------------------------------------------------------
# Tercihin saklanması
# ---------------------------------------------------------------------------


def test_preference_is_absent_before_any_choice():
    assert not i18n.has_stored_preference()


def test_choice_survives_a_restart():
    i18n.set_language("en")
    assert i18n.has_stored_preference()

    # Yeniden başlatmayı taklit et: önbelleği boşalt, tercih diskten okunsun.
    i18n._loaded = False
    i18n._current = DEFAULT_LANGUAGE
    assert i18n.current_language() == "en"


def test_remember_current_records_the_default_too():
    """Varsayılanı seçmek de bir tercihtir; ekran her açılışta çıkmasın."""
    assert not i18n.has_stored_preference()
    assert i18n.remember_current()
    assert i18n.has_stored_preference()


def test_settings_file_lives_under_the_app_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("MM_SETTINGS_DIR", str(tmp_path))
    assert settings.settings_path().parent.name == "MixedMedia"


def test_unreadable_settings_do_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("MM_SETTINGS_DIR", str(tmp_path))
    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{bozuk", encoding="utf-8")
    assert settings.load_settings() == {}
