"""Paketleme ve örnek proje (bkz. CLAUDE.md §11, M7).

Paketlenmiş uygulama iki noktada kaynaktan çalışan sürümden ayrılır: kaynak
dosyalarını nerede aradığı ve harici araçları (FFmpeg, yazı tipi) nerede
bulduğu. Bu testler o yolları kilitler — derlemeyi çalıştırmadan, çünkü
PyInstaller çıktısını CI'da koşturmak pahalıdır.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from mixedmedia.core import example, resources
from mixedmedia.core.page_render import resolve_text_font
from mixedmedia.core.video_probe import find_binary

# ---------------------------------------------------------------------------
# Kaynak çözümleme
# ---------------------------------------------------------------------------


def test_source_checkout_is_not_frozen():
    assert not resources.is_frozen()


def test_directories_exist_when_running_from_source():
    assert resources.app_dir().is_dir()
    assert resources.bundle_dir().is_dir()
    assert (resources.bundle_dir() / "mixedmedia").is_dir()


def test_frozen_layout_uses_the_executable_directory(monkeypatch, tmp_path):
    """Paketlenmiş sürümde uygulama klasörü çalıştırılabilirin yanıdır."""
    executable = tmp_path / "bin" / "MixedMedia.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert resources.is_frozen()
    assert resources.app_dir() == executable.parent
    assert resources.bundle_dir() == bundle


def test_user_directory_takes_priority_over_the_bundle(monkeypatch, tmp_path):
    """Kullanıcının koyduğu dosya, pakete gömülenin önüne geçmeli."""
    app = tmp_path / "app"
    bundle = tmp_path / "bundle"
    (app / "fonts").mkdir(parents=True)
    (bundle / "fonts").mkdir(parents=True)

    monkeypatch.setattr(resources, "app_dir", lambda: app)
    monkeypatch.setattr(resources, "bundle_dir", lambda: bundle)

    assert resources.search_dirs("fonts") == [app / "fonts", bundle / "fonts"]


def test_resource_path_returns_none_when_absent():
    assert resources.resource_path("boyle_bir_dosya_yok.dat") is None


# ---------------------------------------------------------------------------
# Harici araçlar
# ---------------------------------------------------------------------------


def test_ffmpeg_is_found_next_to_the_application(monkeypatch, tmp_path):
    """FFmpeg pakete gömülmez; kullanıcı klasörü yanına bırakabilmeli."""
    app = tmp_path / "app"
    (app / "ffmpeg" / "bin").mkdir(parents=True)
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    dropped = app / "ffmpeg" / "bin" / name
    dropped.write_bytes(b"")

    monkeypatch.delenv("MM_FFMPEG_DIR", raising=False)
    monkeypatch.setattr(resources, "app_dir", lambda: app)
    monkeypatch.setattr(resources, "bundle_dir", lambda: app)

    assert find_binary("ffmpeg") == str(dropped)


def test_environment_variable_beats_the_application_folder(monkeypatch, tmp_path):
    app = tmp_path / "app"
    (app / "ffmpeg").mkdir(parents=True)
    name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    (app / "ffmpeg" / name).write_bytes(b"")

    override = tmp_path / "elle"
    override.mkdir()
    (override / name).write_bytes(b"")

    monkeypatch.setenv("MM_FFMPEG_DIR", str(override))
    monkeypatch.setattr(resources, "app_dir", lambda: app)

    assert find_binary("ffmpeg") == str(override / name)


def test_a_font_dropped_next_to_the_app_wins(monkeypatch, tmp_path):
    """Sistem yazı tipleri lisans gereği gömülmez; kullanıcı kendi .ttf'sini koyabilir."""
    app = tmp_path / "app"
    (app / "fonts").mkdir(parents=True)
    dropped = app / "fonts" / "kendi.ttf"
    dropped.write_bytes(b"")

    monkeypatch.delenv("MM_TEXT_FONT", raising=False)
    monkeypatch.setattr(resources, "app_dir", lambda: app)
    monkeypatch.setattr(resources, "bundle_dir", lambda: app)
    resolve_text_font.cache_clear()

    try:
        assert resolve_text_font() == dropped
    finally:
        resolve_text_font.cache_clear()


# ---------------------------------------------------------------------------
# Örnek proje
# ---------------------------------------------------------------------------


def test_example_project_is_complete(tmp_path):
    """Örnek proje kendi başına çalışır durumda olmalı — video gerekmeden."""
    result = example.create_example_project(tmp_path / "ornek", frame_count=8)

    assert result.pdf_path.is_file()
    assert len(result.project.pages) == 2
    assert result.project.extraction.frame_count == 8

    frames = sorted(result.workspace.frames_dir.glob("*.png"))
    assert len(frames) == 8
    assert len(result.scan_paths) == 2
    assert all(path.is_file() for path in result.scan_paths)


def test_example_project_round_trips(tmp_path):
    """Kabul koşulu: örnek projenin taramaları doğru karelere çözülmeli."""
    from mixedmedia.core import pipeline

    result = example.create_example_project(tmp_path / "ornek", frame_count=8)
    state = pipeline.run_ingest(
        result.workspace, result.project, result.scan_paths
    )

    assert state.found_indices == set(range(8))
    assert all(page.method != "failed" for page in state.pages)


def test_example_second_page_is_deliberately_upside_down(tmp_path):
    """Örnek, ters taranan sayfanın kendiliğinden düzeldiğini göstermeli."""
    from mixedmedia.core import pipeline

    result = example.create_example_project(tmp_path / "ornek", frame_count=8)
    state = pipeline.run_ingest(result.workspace, result.project, result.scan_paths)

    flipped = [page for page in state.pages if page.rotated_180]
    assert len(flipped) == 1
    assert flipped[0].page_no == 2


def test_example_can_skip_the_scans(tmp_path):
    result = example.create_example_project(
        tmp_path / "ornek", frame_count=4, with_scans=False
    )
    assert result.scan_paths == []
    assert result.pdf_path.is_file()


def test_demo_frames_are_distinguishable():
    """Kareler birbirinden ayırt edilebilmeli, yoksa örnek bir şey kanıtlamaz."""
    frames = [np.asarray(example.make_demo_frame(i, 6).convert("L")) for i in range(6)]
    for index, frame in enumerate(frames[1:], start=1):
        difference = np.abs(frame.astype(int) - frames[index - 1].astype(int)).mean()
        assert difference > 1.0, f"kare {index} bir öncekinden ayırt edilemiyor"


def test_example_scans_look_like_scans(tmp_path):
    """Simüle tarama gerçekten bozulmuş olmalı; yoksa export'u sınamaz."""
    result = example.create_example_project(tmp_path / "ornek", frame_count=4)

    scan = cv2.imdecode(
        np.fromfile(result.scan_paths[0], dtype=np.uint8), cv2.IMREAD_COLOR
    )
    medians = [float(np.median(scan[:, :, channel])) for channel in range(3)]
    # Sarı kayma: mavi kanal diğerlerinden sönük olmalı.
    assert medians[0] < medians[1]
    assert scan.shape[0] > 100 and scan.shape[1] > 100


@pytest.mark.parametrize("frames,cols,rows,expected_pages", [(4, 2, 2, 1), (9, 3, 3, 1), (10, 2, 2, 3)])
def test_example_respects_the_requested_grid(tmp_path, frames, cols, rows, expected_pages):
    result = example.create_example_project(
        tmp_path / f"o{frames}", frame_count=frames, cols=cols, rows=rows, with_scans=False
    )
    assert len(result.project.pages) == expected_pages


# ---------------------------------------------------------------------------
# Paketleme tarifi
# ---------------------------------------------------------------------------


def _spec_text() -> str:
    return (Path(__file__).resolve().parents[1] / "packaging" / "mixedmedia.spec").read_text(
        encoding="utf-8"
    )


def test_packaging_files_exist():
    root = Path(__file__).resolve().parents[1] / "packaging"
    assert (root / "mixedmedia.spec").is_file()
    assert (root / "entry_gui.py").is_file()
    assert (root / "entry_cli.py").is_file()


def test_spec_builds_both_executables():
    text = _spec_text()
    assert 'name=APP_NAME' in text
    assert 'name="mm"' in text
    # GUI penceresiz olmalı: arkasında siyah konsol açılmamalı.
    assert "console=False" in text
    assert "console=True" in text


def test_spec_drops_the_unused_heavy_binaries():
    """Bu ayıklama olmadan derleme ~70 MB şişiyor."""
    text = _spec_text()
    for name in ("qt6quick", "opengl32sw", "_avif"):
        assert name in text


def test_entry_points_call_freeze_support():
    """PyInstaller'da alt süreçler uygulamayı yeniden başlatmasın."""
    root = Path(__file__).resolve().parents[1] / "packaging"
    for entry in ("entry_gui.py", "entry_cli.py"):
        assert "freeze_support" in (root / entry).read_text(encoding="utf-8")
