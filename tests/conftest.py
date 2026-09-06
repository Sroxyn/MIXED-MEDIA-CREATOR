"""Ortak test yardımcıları.

Testler ffmpeg'e bağlı olmasın diye kareler PIL ile sentetik üretilir. Her
karede büyük, okunabilir bir kare numarası bulunur — CLAUDE.md §10'daki
round-trip testinin doğrulama zemini budur.
"""

from __future__ import annotations

import os
from pathlib import Path

# Qt testleri ekransiz kosar; QApplication kurulmadan once ayarlanmali.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image, ImageDraw

from mixedmedia.core import layout as layout_mod
from mixedmedia.core.models import (
    Extraction,
    LayoutSettings,
    Project,
    SourceInfo,
)
from mixedmedia.core.page_render import _font
from mixedmedia.core.project import Workspace, save_project

FRAME_SIZE = (320, 180)  # 16:9, testlerin hızlı kalması için küçük


def make_frame(index: int, size: tuple[int, int] = FRAME_SIZE) -> Image.Image:
    """Numarası büyük harflerle yazılmış sentetik bir kare üretir."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    # Kenar çerçevesi: kırpma/hizalama hatalarını gözle görülür kılar.
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(0, 0, 0), width=2)
    draw.text(
        (size[0] // 2, size[1] // 2),
        str(index),
        font=_font(size[1] // 2),
        fill=(0, 0, 0),
        anchor="mm",
    )
    return img


def build_project(
    root: Path,
    frame_count: int = 8,
    *,
    name: str = "Test Sekansı",
    project_id: str = "mm_7f3a9c",
    **layout_kw,
) -> tuple[Workspace, Project]:
    """Diskte kareleri hazır, sayfaları hesaplanmış bir proje kurar."""
    workspace = Workspace(root)
    workspace.ensure_dirs()

    kw = {"cols": 2, "rows": 2}
    kw.update(layout_kw)
    project = Project(
        name=name,
        project_id=project_id,
        source=SourceInfo(
            path="input/synthetic.mp4",
            duration_s=frame_count / 6,
            native_fps=30.0,
            width=FRAME_SIZE[0],
            height=FRAME_SIZE[1],
        ),
        extraction=Extraction(target_fps=6, frame_count=frame_count),
        layout=LayoutSettings(**kw),
    )

    for index in range(frame_count):
        frame = make_frame(index)
        frame.save(workspace.frames_dir / project.frame_filename(index))
        frame.close()

    geom = layout_mod.grid_from_settings(project.layout)
    project.layout.cell_w_mm = round(geom.cell_w_mm, 4)
    project.layout.cell_h_mm = round(geom.cell_h_mm, 4)
    project.pages = layout_mod.build_pages(project)
    save_project(workspace, project)
    return workspace, project


@pytest.fixture
def project_dir(tmp_path: Path):
    """8 kareli, 2×2 ızgaralı hazır bir proje (2 sayfa)."""
    return build_project(tmp_path / "proj", frame_count=8)


@pytest.fixture(scope="session")
def qapp():
    """Testler boyunca tek bir QApplication (ekransiz platformda)."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])
