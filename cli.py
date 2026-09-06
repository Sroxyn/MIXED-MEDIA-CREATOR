"""MixedMedia komut satırı arayüzü.

``core``'un tamamı buradan erişilebilir olmalıdır — GUI yalnızca aynı
fonksiyonları sürer. Bu, testleri ve hata ayıklamayı mümkün kılar
(bkz. CLAUDE.md §4, §9).
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import unicodedata
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from .core import (
    example,
    frame_extract,
    layout as layout_mod,
    pdf_writer,
    pipeline,
    project as project_mod,
)
from .core.constants import (
    CELLS_PER_PAGE_PRESETS,
    LARGE_JOB_PAGE_WARN,
    MIN_EFFECTIVE_DPI,
)
from .core.errors import MixedMediaError, OperationCancelled
from .core.markers import validate_marker_budget
from .core.models import (
    Codec,
    CutMarks,
    ImageFit,
    MissingFramePolicy,
    Orientation,
    PaperSize,
    ProcessingMode,
    Project,
)
from .core.page_render import resolve_text_font
from .core.resources import app_dir, is_frozen
from .core.video_probe import ffmpeg_available


def _configure_console() -> None:
    """Çıktıyı UTF-8'e ayarlar.

    Windows konsolu varsayılan olarak cp1254 kullanır; "→" ve "✓" gibi
    karakterler orada kodlanamaz ve yazdırma çökerdi. Kod sayfasını UTF-8'e
    almayı deneriz, başarısız olursa ``errors="replace"`` ile yumuşak iniş
    yaparız — hiçbir durumda çıktı yüzünden çökmeyiz.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:  # pragma: no cover - konsolsuz ortam
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - yönlendirilmiş akış
            pass


_configure_console()

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="MixedMedia Round-Trip Studio — video → kağıt → video.",
)

_PROJECT_OPT = typer.Option(
    Path("."), "--project", "-p", help="Proje klasörü veya project.mmp.json."
)


# ---------------------------------------------------------------------------
# Ortak yardımcılar
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=err_console, rich_tracebacks=False, show_path=verbose)],
    )


def slugify(name: str) -> str:
    """Proje adından klasör adı üretir (Türkçe karakterler sadeleştirilir)."""
    folded = name.replace("ı", "i").replace("İ", "i").replace("ğ", "g").replace("Ğ", "g")
    folded = folded.replace("ş", "s").replace("Ş", "s").replace("ö", "o").replace("Ö", "o")
    folded = folded.replace("ü", "u").replace("Ü", "u").replace("ç", "c").replace("Ç", "c")
    ascii_name = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_name).strip("_").lower()
    return slug or "proje"


def _load(path: Path) -> tuple[project_mod.Workspace, Project]:
    return project_mod.load_project(path)


def _fail(exc: MixedMediaError) -> None:
    """Kullanıcı diliyle hata yazdırıp çıkar."""
    err_console.print(Panel(str(exc), title="Hata", border_style="red"))
    raise typer.Exit(code=1)


def _progress_bar(description: str) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def _summarize_layout(project: Project) -> Table:
    """Yerleşim özet tablosu; efektif DPI uyarısını da içerir."""
    lay = project.layout
    geom = layout_mod.grid_from_settings(lay)
    aspect = project.source.aspect_ratio if project.source else 1.0
    box = layout_mod.fit_image_box(geom.cell_at(0), aspect, lay.image_fit)
    pages = layout_mod.page_count(project.extraction.frame_count, geom.cells_per_page)

    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_row("Kağıt", f"{lay.paper.value} {lay.orientation.value} @ {lay.dpi} DPI")
    table.add_row("Izgara", f"{lay.cols} × {lay.rows}  ({geom.cells_per_page} kare/sayfa)")
    table.add_row("Hücre", f"{geom.cell_w_mm:.1f} × {geom.cell_h_mm:.1f} mm")
    table.add_row("Görüntü", f"{box.w_mm:.1f} × {box.h_mm:.1f} mm ({lay.image_fit.value})")
    table.add_row("Boşluklar", f"kenar {lay.margin_mm:g} mm · ara {lay.gutter_mm:g} mm")
    table.add_row("Sayfa sayısı", str(pages))

    if project.source:
        dpi = layout_mod.effective_dpi(project.source.display_width, box.w_mm)
        style = "yellow" if dpi < MIN_EFFECTIVE_DPI else "green"
        note = " — baskıda yumuşak görünecek" if dpi < MIN_EFFECTIVE_DPI else ""
        table.add_row("Efektif DPI", f"[{style}]{dpi:.0f}{note}[/{style}]")

    marks = []
    if lay.markers_enabled:
        marks.append("köşe + QR")
    if lay.cell_marker_band_mm:
        marks.append("hücre marker'ı")
    if lay.calibration_strip:
        marks.append("kalibrasyon")
    if lay.footer_text:
        marks.append("footer")
    table.add_row("Ekler", ", ".join(marks) if marks else "[yellow]yok (manuel mod)[/yellow]")
    return table


# ---------------------------------------------------------------------------
# mm new
# ---------------------------------------------------------------------------


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Proje adı."),
    video: Optional[Path] = typer.Option(None, "--video", "-v", help="Kaynak video."),
    directory: Optional[Path] = typer.Option(
        None, "--dir", "-d", help="Proje klasörü (varsayılan: ada göre türetilir)."
    ),
    copy_video: bool = typer.Option(
        True, "--copy/--link", help="Videoyu input/ altına kopyala ya da yerinde bağla."
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Var olan projenin üzerine yaz."),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Yeni bir proje oluşturur ve isteğe bağlı olarak video bağlar."""
    _setup_logging(verbose)
    ok, detail = ffmpeg_available()
    if not ok and video is not None:
        err_console.print(f"[yellow]Uyarı:[/yellow] FFmpeg bulunamadı ({detail}).")

    root = Path(directory) if directory else Path.cwd() / slugify(name)
    try:
        workspace, proj = project_mod.create_project(
            root, name, video, copy_video=copy_video, overwrite=overwrite
        )
    except MixedMediaError as exc:
        _fail(exc)

    console.print(f"[green]✓[/green] Proje oluşturuldu: [bold]{workspace.root}[/bold]")
    console.print(f"  Kimlik: {proj.project_id}")
    if proj.source:
        src = proj.source
        console.print(
            f"  Video: {src.path} — {src.duration_s:.2f} sn · {src.native_fps:.3f} fps · "
            f"{src.display_width}×{src.display_height}"
            + (f" · rotation {src.rotation}°" if src.rotation else "")
            + (" · sesli" if src.has_audio else " · sessiz")
        )
        estimated = frame_extract.expected_frame_count(
            src.duration_s, proj.extraction.target_fps
        )
        console.print(
            f"  {proj.extraction.target_fps:g} fps ile ≈ {estimated} kare çıkacak "
            f"('mm extract' ile başlatın)."
        )


# ---------------------------------------------------------------------------
# mm extract
# ---------------------------------------------------------------------------


@app.command("extract")
def cmd_extract(
    project_path: Path = _PROJECT_OPT,
    fps: Optional[float] = typer.Option(None, "--fps", help="Hedef kare hızı."),
    max_width: Optional[int] = typer.Option(
        None, "--max-width", help="Kareleri bu genişliğe küçült (lanczos)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onay sorma."),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Videoyu seçilen FPS'te karelere böler."""
    _setup_logging(verbose)
    try:
        workspace, proj = _load(project_path)
        if proj.source is None:
            raise MixedMediaError(
                "Projeye kaynak video bağlanmamış. 'mm new --video ...' kullanın."
            )
        if fps is not None:
            proj.extraction.target_fps = fps

        expected = frame_extract.expected_frame_count(
            proj.source.duration_s, proj.extraction.target_fps
        )
        pages = layout_mod.page_count(expected, proj.layout.cells_per_page)
        summary = (
            f"{proj.source.duration_s:.1f} sn @ {proj.extraction.target_fps:g} fps "
            f"= {expected} kare = {pages} sayfa {proj.layout.paper.value}"
        )
        console.print(summary)
        if not yes and pages > LARGE_JOB_PAGE_WARN:
            if not typer.confirm("Devam edilsin mi?", default=True):
                raise OperationCancelled("Kare çıkarma")

        cancel = threading.Event()
        with _progress_bar("Kareler çıkarılıyor") as bar:
            task = bar.add_task("Kareler çıkarılıyor", total=expected)

            def on_progress(update: frame_extract.ExtractProgress) -> None:
                bar.update(task, completed=min(update.frames_done, expected))

            count = frame_extract.extract_frames(
                proj, workspace.root, max_width=max_width, progress=on_progress, cancel=cancel
            )
            bar.update(task, completed=expected)

        proj.extraction.frame_count = count
        proj.pages = []  # kare sayısı değişti; yerleşim yeniden hesaplanmalı
        project_mod.save_project(workspace, proj)
    except MixedMediaError as exc:
        _fail(exc)

    console.print(f"[green]✓[/green] {count} kare çıkarıldı → {workspace.frames_dir}")
    if count != expected:
        console.print(
            f"[yellow]Not:[/yellow] beklenen {expected} kare yerine {count} kare üretildi "
            "(yuvarlama farkı normaldir)."
        )
    console.print("  Sonraki adım: 'mm layout'")


# ---------------------------------------------------------------------------
# mm layout
# ---------------------------------------------------------------------------


@app.command("layout")
def cmd_layout(
    project_path: Path = _PROJECT_OPT,
    paper: Optional[PaperSize] = typer.Option(None, "--paper", help="A4 veya A3."),
    orientation: Optional[Orientation] = typer.Option(
        None, "--orientation", help="portrait veya landscape."
    ),
    per_page: Optional[int] = typer.Option(
        None, "--per-page", help=f"Sayfa başına kare (otomatik ızgara): {CELLS_PER_PAGE_PRESETS}."
    ),
    cols: Optional[int] = typer.Option(None, "--cols", help="Manuel sütun sayısı."),
    rows: Optional[int] = typer.Option(None, "--rows", help="Manuel satır sayısı."),
    margin: Optional[float] = typer.Option(None, "--margin", help="Kenar boşluğu (mm)."),
    gutter: Optional[float] = typer.Option(None, "--gutter", help="Hücre arası boşluk (mm)."),
    dpi: Optional[int] = typer.Option(None, "--dpi", help="Baskı çözünürlüğü."),
    fit: Optional[ImageFit] = typer.Option(None, "--fit", help="contain veya cover."),
    mode: Optional[ProcessingMode] = typer.Option(None, "--mode", help="Baskı işleme modu."),
    density: Optional[float] = typer.Option(
        None, "--density", min=0.0, max=1.0, help="Baskı yoğunluğu (0 = çok açık)."
    ),
    cut_marks: Optional[CutMarks] = typer.Option(None, "--cut-marks", help="Kesim işaretleri."),
    markers: Optional[bool] = typer.Option(
        None, "--markers/--no-markers", help="Sayfa fiducial'ları."
    ),
    cell_markers: Optional[bool] = typer.Option(
        None, "--cell-markers/--no-cell-markers", help="Hücre başına mikro-marker."
    ),
    calibration: Optional[bool] = typer.Option(
        None, "--calibration/--no-calibration", help="Gri skala kalibrasyon şeridi."
    ),
    footer: Optional[bool] = typer.Option(None, "--footer/--no-footer", help="Footer metni."),
    lock_orientation: bool = typer.Option(
        False, "--lock-orientation", help="Otomatik modda yönü değiştirme."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Sayfa yerleşimini hesaplar ve hücre koordinatlarını projeye yazar."""
    _setup_logging(verbose)
    try:
        workspace, proj = _load(project_path)
        lay = proj.layout

        for field_name, value in (
            ("paper", paper),
            ("orientation", orientation),
            ("margin_mm", margin),
            ("gutter_mm", gutter),
            ("dpi", dpi),
            ("image_fit", fit),
            ("cut_marks", cut_marks),
            ("markers_enabled", markers),
            ("cell_markers_enabled", cell_markers),
            ("calibration_strip", calibration),
            ("footer_text", footer),
        ):
            if value is not None:
                setattr(lay, field_name, value)

        if mode is not None:
            proj.processing.mode = mode
        if density is not None:
            proj.processing.print_density = density

        if cols is not None or rows is not None:
            if cols is None or rows is None:
                raise MixedMediaError("Manuel modda --cols ve --rows birlikte verilmeli.")
            lay.cols, lay.rows = cols, rows
        elif per_page is not None:
            aspect = proj.source.aspect_ratio if proj.source else 1.0
            best = layout_mod.choose_best_grid_for_settings(
                lay, aspect, per_page, lock_orientation=lock_orientation
            )
            lay.orientation = best.orientation
            lay.cols, lay.rows = best.cols, best.rows

        validate_marker_budget(lay.cells_per_page)

        geom = layout_mod.grid_from_settings(lay)
        lay.cell_w_mm = round(geom.cell_w_mm, 4)
        lay.cell_h_mm = round(geom.cell_h_mm, 4)
        proj.pages = layout_mod.build_pages(proj)
        project_mod.save_project(workspace, proj)
    except (MixedMediaError, ValueError) as exc:
        _fail(exc if isinstance(exc, MixedMediaError) else MixedMediaError(str(exc)))

    console.print(_summarize_layout(proj))
    if not proj.pages:
        console.print(
            "[yellow]Henüz kare yok[/yellow] — yerleşim kaydedildi, "
            "sayfalar 'mm extract' sonrası oluşur."
        )
    else:
        console.print(
            f"[green]✓[/green] {len(proj.pages)} sayfa · {proj.total_cells} hücre yazıldı."
        )
        console.print("  Sonraki adım: 'mm pdf'")


# ---------------------------------------------------------------------------
# mm pdf
# ---------------------------------------------------------------------------


@app.command("pdf")
def cmd_pdf(
    project_path: Path = _PROJECT_OPT,
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Çıktı PDF yolu."),
    dpi: Optional[int] = typer.Option(None, "--dpi", help="PNG çıktısı için çözünürlük."),
    png: bool = typer.Option(False, "--png", help="PDF yerine sayfa başına PNG üret."),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Baskıya hazır PDF (veya sayfa PNG'leri) üretir."""
    _setup_logging(verbose)
    try:
        workspace, proj = _load(project_path)
        if not proj.pages:
            raise MixedMediaError(
                "Projede sayfa yok. Önce 'mm extract' ve 'mm layout' çalıştırın."
            )
        resolution = dpi or proj.layout.dpi
        total = len(proj.pages)
        cancel = threading.Event()

        with _progress_bar("Sayfalar işleniyor") as bar:
            task = bar.add_task("Sayfalar işleniyor", total=total)

            def on_progress(update: pdf_writer.PdfProgress) -> None:
                bar.update(task, completed=update.page_done)

            if png:
                target_dir = Path(out) if out else workspace.out_dir / "pages"
                written = pdf_writer.write_page_pngs(
                    proj,
                    workspace.root,
                    target_dir,
                    resolution,
                    progress=on_progress,
                    cancel=cancel,
                )
                result = f"{len(written)} PNG → {target_dir}"
            else:
                target = Path(out) if out else workspace.out_dir / f"{slugify(proj.name)}.pdf"
                pdf_writer.write_pdf(
                    proj, workspace.root, target, progress=on_progress, cancel=cancel
                )
                size_mb = target.stat().st_size / (1024 * 1024)
                result = f"{target} ({total} sayfa, {size_mb:.1f} MB)"
    except MixedMediaError as exc:
        _fail(exc)

    console.print(f"[green]✓[/green] {result}")


# ---------------------------------------------------------------------------
# mm ingest
# ---------------------------------------------------------------------------


@app.command("ingest")
def cmd_ingest(
    scans: list[Path] = typer.Argument(..., help="Tarama dosyaları ve/veya klasörleri."),
    project_path: Path = _PROJECT_OPT,
    bleed: Optional[float] = typer.Option(
        None, "--bleed", help="Kesim payı (mm). Negatif = içeriden kes."
    ),
    work_dpi: Optional[int] = typer.Option(
        None, "--work-dpi", help="Rektifikasyon çözünürlüğü."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Taranan sayfaları düzeltip kareleri çıkarır."""
    _setup_logging(verbose)
    try:
        workspace, proj = _load(project_path)
        cancel = threading.Event()
        with _progress_bar("Taramalar işleniyor") as bar:
            task = bar.add_task("Taramalar işleniyor", total=None)

            def on_progress(update: pipeline.IngestProgress) -> None:
                bar.update(task, total=update.scans_total, completed=update.scans_done)

            state = pipeline.run_ingest(
                workspace,
                proj,
                scans,
                bleed_mm=bleed,
                work_dpi=work_dpi,
                progress=on_progress,
                cancel=cancel,
            )
    except MixedMediaError as exc:
        _fail(exc)

    console.print(_ingest_table(proj, state))
    missing = sorted(set(range(proj.extraction.frame_count)) - state.found_indices)
    if missing:
        console.print(
            f"[yellow]{len(missing)} kare eksik[/yellow] — 'mm cells' ile ayrıntıya bakın."
        )
    else:
        console.print("[green]✓[/green] Tüm kareler bulundu.")
    console.print("  Sonraki adım: 'mm video'")


def _ingest_table(project: Project, state) -> Table:
    """Sayfa başına tespit rozetleri (yeşil/sarı/kırmızı)."""
    table = Table(title="Taranan sayfalar", title_justify="left")
    table.add_column("Sayfa", justify="right")
    table.add_column("Durum")
    table.add_column("Yöntem")
    table.add_column("İşaret")
    table.add_column("Kaynak")
    table.add_column("Not")

    badge = {"high": "[green]●[/green] iyi", "medium": "[yellow]●[/yellow] orta"}
    for page in state.pages:
        if page.method == "failed":
            status = "[red]●[/red] okunamadı"
        else:
            status = badge.get(page.confidence, "[red]●[/red] zayıf")
        note = page.warnings[0] if page.warnings else ""
        table.add_row(
            str(page.page_no),
            status,
            page.method,
            f"{page.corners_found}+{page.cell_markers_found}",
            page.source,
            note,
        )

    found = len(state.found_indices)
    table.caption = (
        f"{found}/{project.extraction.frame_count} kare çıkarıldı → {state.cell_dir}"
    )
    return table


# ---------------------------------------------------------------------------
# mm cells
# ---------------------------------------------------------------------------


@app.command("cells")
def cmd_cells(
    project_path: Path = _PROJECT_OPT,
    show_all: bool = typer.Option(False, "--all", help="Bulunan kareleri de listele."),
) -> None:
    """Tespit raporunu yazdırır: hangi kare bulundu, hangisi eksik."""
    try:
        workspace, proj = _load(project_path)
    except MixedMediaError as exc:
        _fail(exc)

    state = proj.ingest
    if state is None or not state.cells:
        console.print("[yellow]Henüz tarama alınmamış.[/yellow] Önce 'mm ingest' çalıştırın.")
        raise typer.Exit(code=1)

    console.print(_ingest_table(proj, state))

    by_index = {cell.frame_index: cell for cell in state.cells}
    missing = [i for i in range(proj.extraction.frame_count) if i not in by_index]
    low = sorted(state.low_confidence_indices)

    table = Table(title="Kareler", title_justify="left")
    table.add_column("Kare", justify="right")
    table.add_column("Durum")
    table.add_column("Sayfa", justify="right")
    table.add_column("Kaynak")
    for index in range(proj.extraction.frame_count):
        cell = by_index.get(index)
        if cell is None:
            table.add_row(str(index), "[red]eksik[/red]", "—", "—")
        elif not show_all and cell.confidence != "low":
            continue
        else:
            style = "yellow" if cell.confidence == "low" else "green"
            table.add_row(
                str(index),
                f"[{style}]{cell.confidence}[/{style}]",
                str(cell.page_no),
                cell.source,
            )
    if missing or low or show_all:
        console.print(table)

    console.print(
        f"Toplam {proj.extraction.frame_count} kare · "
        f"[green]{len(by_index)} bulundu[/green] · "
        f"[red]{len(missing)} eksik[/red] · "
        f"[yellow]{len(low)} düşük güvenli[/yellow]"
    )
    if missing:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# mm video
# ---------------------------------------------------------------------------


@app.command("video")
def cmd_video(
    project_path: Path = _PROJECT_OPT,
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Çıktı dosyası."),
    fps: Optional[float] = typer.Option(None, "--fps", help="Çıktı kare hızı."),
    codec: Optional[Codec] = typer.Option(None, "--codec", help="h264 / prores / png_seq."),
    width: Optional[int] = typer.Option(None, "--width", help="Çıktı genişliği."),
    height: Optional[int] = typer.Option(None, "--height", help="Çıktı yüksekliği."),
    stabilize: Optional[float] = typer.Option(
        None,
        "--stabilize",
        min=0.0,
        max=1.0,
        help="Kağıt titremesini siler, hareketi korur. 0 = ham hâli, 1 = tam düzelt.",
    ),
    policy: Optional[MissingFramePolicy] = typer.Option(
        None, "--missing", help="Eksik kare politikası."
    ),
    audio: Optional[bool] = typer.Option(
        None, "--audio/--no-audio", help="Orijinal sesi yeniden ekle."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Çıkarılmış kareleri videoya çevirir."""
    _setup_logging(verbose)
    try:
        workspace, proj = _load(project_path)
        if policy is not None:
            proj.export.missing_frame_policy = policy

        chosen_codec = codec or proj.export.codec
        target = Path(out) if out else _default_video_path(workspace, proj, chosen_codec)
        resolution = (width, height) if width and height else None
        output_fps = fps or proj.export.output_fps

        _warn_if_speed_changes(proj, output_fps)

        cancel = threading.Event()
        with _progress_bar("Video üretiliyor") as bar:
            task = bar.add_task("Kareler hazırlanıyor", total=None)

            labels = {
                "analyse": "Hareket çözümleniyor",
                "frames": "Kareler hazırlanıyor",
                "encode": "Kodlanıyor",
            }

            def on_progress(update) -> None:
                bar.update(
                    task,
                    description=labels.get(update.stage, "İşleniyor"),
                    total=update.total,
                    completed=update.done,
                )

            written, report = pipeline.run_export(
                workspace,
                proj,
                target,
                fps=output_fps,
                codec=chosen_codec,
                resolution=resolution,
                stabilize=stabilize,
                reattach_audio=audio,
                progress=on_progress,
                cancel=cancel,
            )
        project_mod.save_project(workspace, proj)
    except MixedMediaError as exc:
        _fail(exc)

    console.print(f"[green]✓[/green] {written}")
    console.print(f"  {report.summary()} · {report.output_length} kare yazıldı")
    for note in report.notes:
        console.print(f"  [yellow]Not:[/yellow] {note}")
    if report.missing:
        console.print(
            f"  [yellow]Eksikler '{proj.export.missing_frame_policy.value}' "
            "politikasıyla dolduruldu.[/yellow]"
        )


def _default_video_path(workspace, project: Project, codec: Codec) -> Path:
    """Çıktı adı verilmediğinde kullanılacak yol."""
    stem = slugify(project.name) + "_final"
    if codec is Codec.PNG_SEQ:
        return workspace.out_dir / stem
    suffix = ".mov" if codec is Codec.PRORES else ".mp4"
    return workspace.out_dir / (stem + suffix)


def _warn_if_speed_changes(project: Project, output_fps: float) -> None:
    """Çıkarma ve oynatma FPS'i farklıysa hız değişimini açıkça göster (§7.7)."""
    source_fps = project.extraction.target_fps
    if abs(output_fps - source_fps) < 1e-6:
        return
    factor = output_fps / source_fps
    console.print(
        f"[yellow]Not:[/yellow] {source_fps:g} fps'te çıkarılan kareler "
        f"{output_fps:g} fps'te oynatılacak — hareket [bold]{factor:.2g}×[/bold] "
        + ("hızlanacak." if factor > 1 else "yavaşlayacak.")
    )


# ---------------------------------------------------------------------------
# mm info / doctor
# ---------------------------------------------------------------------------


@app.command("info")
def cmd_info(project_path: Path = _PROJECT_OPT) -> None:
    """Projenin durumunu özetler."""
    try:
        workspace, proj = _load(project_path)
    except MixedMediaError as exc:
        _fail(exc)

    console.print(Panel(f"[bold]{proj.name}[/bold]  ({proj.project_id})", border_style="cyan"))
    if proj.source:
        src = proj.source
        console.print(
            f"Video: {src.path} — {src.duration_s:.2f} sn · {src.native_fps:.3f} fps · "
            f"{src.display_width}×{src.display_height}"
        )
    frames = frame_extract.list_frames(workspace.frames_dir, proj.extraction.naming)
    console.print(
        f"Kareler: {len(frames)} dosya (kayıtlı: {proj.extraction.frame_count}) "
        f"@ {proj.extraction.target_fps:g} fps"
    )
    console.print(_summarize_layout(proj))


@app.command("example")
def cmd_example(
    directory: Optional[Path] = typer.Argument(
        None, help="Örnek projenin kurulacağı klasör."
    ),
    frames: int = typer.Option(12, "--frames", min=1, max=200, help="Kare sayısı."),
    paper: PaperSize = typer.Option(PaperSize.A4, "--paper", help="A4 veya A3."),
    no_scans: bool = typer.Option(
        False, "--no-scans", help="Simüle tarama üretme (yalnızca baskı tarafı)."
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Ayrıntılı günlük."),
) -> None:
    """Çalışır durumda bir örnek proje kurar — kendi videonuz gerekmez."""
    _setup_logging(verbose)
    root = Path(directory) if directory else Path.cwd() / "mixedmedia_ornek"
    try:
        result = example.create_example_project(
            root, frame_count=frames, paper=paper, with_scans=not no_scans
        )
    except MixedMediaError as exc:
        _fail(exc)

    console.print(f"[green]✓[/green] Örnek proje kuruldu: [bold]{result.workspace.root}[/bold]")
    console.print(
        f"  {frames} kare · {len(result.project.pages)} sayfa {paper.value} · "
        f"{result.pdf_path.name}"
    )
    if result.scan_paths:
        console.print(
            f"  {len(result.scan_paths)} simüle tarama → {result.workspace.scans_dir}"
        )
        console.print("\n  Döngüyü tamamlamak için:")
        console.print(f"    mm ingest {result.workspace.scans_dir} -p {result.workspace.root}")
        console.print(f"    mm video -p {result.workspace.root} --codec png_seq")
    else:
        console.print(f"\n  Baskıyı görmek için: {result.pdf_path}")


@app.command("gui")
def cmd_gui(
    project_path: Optional[Path] = typer.Argument(
        None, help="Açılışta yüklenecek proje klasörü."
    ),
) -> None:
    """Grafik arayüzü açar."""
    try:
        from .ui.app import main as gui_main
    except ImportError as exc:  # pragma: no cover - PySide6 kurulu değilse
        _fail(
            MixedMediaError(
                "Grafik arayüz açılamadı: PySide6 kurulu değil.\n"
                "Kurulum: pip install PySide6",
                detail=str(exc),
            )
        )
    argv = ["mixedmedia"] + ([str(project_path)] if project_path else [])
    raise typer.Exit(code=gui_main(argv))


@app.command("doctor")
def cmd_doctor(project_path: Optional[Path] = _PROJECT_OPT) -> None:
    """Ortamı ve projeyi denetler: FFmpeg, yazı tipi, eksik kare, eksik sayfa."""
    console.print(
        f"[dim]·[/dim] Sürüm — {'paketlenmiş' if is_frozen() else 'kaynaktan'} "
        f"· {app_dir()}"
    )

    ok, detail = ffmpeg_available()
    console.print(f"{'[green]✓[/green]' if ok else '[red]✗[/red]'} FFmpeg — {detail}")
    if not ok:
        console.print(
            "    Kurun ([link=https://ffmpeg.org/download.html]ffmpeg.org[/link]) ya da "
            f"klasörü şuraya bırakın: {app_dir() / 'ffmpeg'}"
        )

    font = resolve_text_font()
    if font is None:
        console.print(
            "[yellow]![/yellow] Yazı tipi — Unicode kapsayan bir yazı tipi bulunamadı; "
            "PDF'teki Türkçe karakterler bozulabilir"
        )
        console.print(f"    Bir .ttf bırakın: {app_dir() / 'fonts'}")
    else:
        console.print(f"[green]✓[/green] Yazı tipi — {font}")

    try:
        workspace, proj = _load(project_path)
    except MixedMediaError as exc:
        console.print(f"[yellow]—[/yellow] Proje okunamadı: {exc.message}")
        raise typer.Exit(code=0 if ok else 1)

    missing = [
        cell.frame_file
        for page in proj.pages
        for cell in page.cells
        if not workspace.resolve(cell.frame_file).is_file()
    ]
    console.print(
        f"{'[green]✓[/green]' if not missing else '[red]✗[/red]'} "
        f"Kare dosyaları — {proj.total_cells - len(missing)}/{proj.total_cells} mevcut"
    )
    for path in missing[:10]:
        console.print(f"    eksik: {path}")
    if len(missing) > 10:
        console.print(f"    … ve {len(missing) - 10} tane daha")

    expected_pages = layout_mod.page_count(
        proj.extraction.frame_count, proj.layout.cells_per_page
    )
    stale = len(proj.pages) != expected_pages
    console.print(
        f"{'[yellow]![/yellow]' if stale else '[green]✓[/green]'} "
        f"Yerleşim — {len(proj.pages)} sayfa kayıtlı, {expected_pages} bekleniyor"
        + ("  ('mm layout' ile tazeleyin)" if stale else "")
    )

    state = proj.ingest
    problems = bool(missing or stale or not ok)
    if state is None or not state.cells:
        console.print("[dim]—[/dim] Tarama — henüz alınmamış ('mm ingest')")
    else:
        found = state.found_indices
        gaps = [i for i in range(proj.extraction.frame_count) if i not in found]
        unread = [page for page in state.pages if page.method == "failed"]
        console.print(
            f"{'[green]✓[/green]' if not gaps else '[yellow]![/yellow]'} "
            f"Taranan kareler — {len(found)}/{proj.extraction.frame_count} bulundu"
            + (f", {len(gaps)} eksik" if gaps else "")
        )
        for page in unread:
            console.print(f"    [red]okunamadı:[/red] {page.source}")
        scanned = {page.page_no for page in state.pages if page.method != "failed"}
        absent = [page.page_no for page in proj.pages if page.page_no not in scanned]
        if absent:
            console.print(f"    taranmamış sayfa: {_compact_numbers(absent)}")
            problems = True
        low = sorted(state.low_confidence_indices)
        if low:
            console.print(f"    [yellow]düşük güvenli kare:[/yellow] {_compact_numbers(low)}")

    if problems:
        raise typer.Exit(code=1)


def _compact_numbers(values: list[int], limit: int = 12) -> str:
    """Uzun sayı listelerini kısaltarak gösterir."""
    shown = ", ".join(str(value) for value in values[:limit])
    return shown if len(values) <= limit else f"{shown} … (+{len(values) - limit})"


def main() -> None:
    """Konsol giriş noktası (``mm``)."""
    try:
        app()
    except MixedMediaError as exc:  # pragma: no cover - komutlar zaten yakalıyor
        err_console.print(Panel(str(exc), title="Hata", border_style="red"))
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
