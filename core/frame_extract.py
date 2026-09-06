"""ffmpeg ile kare çıkarma (bkz. CLAUDE.md §6.2).

Kayıpsız PNG üretilir; JPEG kullanılmaz çünkü baskıda artefakt görünür.
İşlem iptal edilebilir ve ilerleme geri çağrımı ile raporlanır. Kareler asla
topluca belleğe alınmaz — ffmpeg doğrudan diske yazar.
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .errors import FrameExtractionFailed, OperationCancelled
from .i18n import t
from .models import Project
from .video_probe import require_ffmpeg

log = logging.getLogger(__name__)

ProgressCallback = Callable[["ExtractProgress"], None]

_PNG_COMPRESSION_LEVEL = 3  # hız/boyut dengesi; kayıpsız
_KILL_GRACE_S = 5


@dataclass(frozen=True)
class ExtractProgress:
    """Kare çıkarma ilerlemesi."""

    frames_done: int
    frames_total: int
    speed: str = ""

    @property
    def percent(self) -> float:
        if self.frames_total <= 0:
            return 0.0
        return min(100.0, 100.0 * self.frames_done / self.frames_total)


def expected_frame_count(duration_s: float, target_fps: float) -> int:
    """``ceil(duration * fps)`` — beklenen kare sayısı (CLAUDE.md §6.2)."""
    return max(1, math.ceil(duration_s * target_fps))


def build_extract_command(
    ffmpeg: str,
    source: Path,
    out_pattern: Path,
    target_fps: float,
    *,
    max_width: int | None = None,
) -> list[str]:
    """ffmpeg komutunu kurar. Saf fonksiyon — test edilebilir olsun diye ayrıldı.

    ``-fps_mode passthrough`` ffmpeg'in kare dublesi yapmasını engeller
    (eski ``-vsync 0``'ın güncel karşılığı). Ölçekleme istenirse lanczos
    kullanılır; en-boy oranı ``-2`` ile korunur.
    """
    filters = [f"fps={target_fps:g}"]
    if max_width:
        filters.append(f"scale={int(max_width)}:-2:flags=lanczos")

    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-vf",
        ",".join(filters),
        "-fps_mode",
        "passthrough",
        "-compression_level",
        str(_PNG_COMPRESSION_LEVEL),
        "-progress",
        "pipe:1",
        "-nostats",
        "-loglevel",
        "error",
        str(out_pattern),
    ]


def clear_frame_dir(frame_dir: Path, naming: str) -> int:
    """Önceki çıkarmadan kalan kareleri siler; silinen dosya sayısını döner.

    Yalnızca projenin kendi adlandırma şablonuna uyan dosyalar silinir; klasöre
    elle konmuş başka içerik korunur.
    """
    if not frame_dir.is_dir():
        return 0
    pattern = re.sub(r"%0?\d*d", "*", naming)
    stale = sorted(frame_dir.glob(pattern))
    for path in stale:
        path.unlink()
    if stale:
        log.info("%d eski kare silindi: %s", len(stale), frame_dir)
    return len(stale)


def list_frames(frame_dir: Path, naming: str) -> list[Path]:
    """Klasördeki kareleri ada göre sıralı döner."""
    if not frame_dir.is_dir():
        return []
    pattern = re.sub(r"%0?\d*d", "*", naming)
    return sorted(frame_dir.glob(pattern))


def extract_frames(
    project: Project,
    root: Path,
    *,
    max_width: int | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> int:
    """Videoyu hedef FPS'te karelere böler ve gerçek kare sayısını döner.

    ``project.extraction.frame_count`` çağıran tarafından güncellenmelidir
    (projeyi kaydetme sorumluluğu ``project.py``'dedir).

    Raises:
        FrameExtractionFailed: ffmpeg hata verirse veya hiç kare üretilmezse.
        OperationCancelled: ``cancel`` olayı tetiklenirse.
    """
    if project.source is None:
        raise FrameExtractionFailed(t("Projeye henüz bir kaynak video bağlanmamış."))

    source = Path(project.source.path)
    if not source.is_absolute():
        source = (root / source).resolve()
    if not source.is_file():
        raise FrameExtractionFailed(
            t(
                "Kaynak video bulunamadı: {path}\n"
                "Video taşınmış veya silinmiş olabilir; projeyi yeniden bağlayın.",
                path=source,
            )
        )

    frame_dir = root / project.extraction.frame_dir
    frame_dir.mkdir(parents=True, exist_ok=True)
    clear_frame_dir(frame_dir, project.extraction.naming)

    total = expected_frame_count(project.source.duration_s, project.extraction.target_fps)
    cmd = build_extract_command(
        require_ffmpeg(),
        source,
        frame_dir / project.extraction.naming,
        project.extraction.target_fps,
        max_width=max_width,
    )
    log.info("Kare çıkarma başlıyor (~%d kare): %s", total, " ".join(cmd))

    stderr_tail = _run_with_progress(cmd, total, progress, cancel, frame_dir, project)

    produced = list_frames(frame_dir, project.extraction.naming)
    if not produced:
        raise FrameExtractionFailed(
            t("ffmpeg hiç kare üretemedi. Video kodeği desteklenmiyor olabilir."),
            detail=stderr_tail,
        )
    if len(produced) != total:
        # Yuvarlama farkı normaldir; büyük sapma veri kaybına işaret eder.
        log.warning(
            "Kare sayısı uyuşmazlığı: beklenen %d, üretilen %d (%s)",
            total,
            len(produced),
            source.name,
        )
    log.info("%d kare çıkarıldı: %s", len(produced), frame_dir)
    return len(produced)


def _run_with_progress(
    cmd: list[str],
    total: int,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
    frame_dir: Path,
    project: Project,
) -> str:
    """ffmpeg'i çalıştırır, ``-progress`` akışını okur, iptali uygular."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    stderr_lines: list[str] = []
    reader = threading.Thread(
        target=_drain, args=(proc.stderr, stderr_lines), daemon=True, name="ffmpeg-stderr"
    )
    reader.start()

    frames_done, speed = 0, ""
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "frame":
                frames_done = int(value) if value.isdigit() else frames_done
            elif key == "speed":
                speed = value
            elif key == "progress":
                if progress:
                    progress(ExtractProgress(frames_done, total, speed))
            if cancel is not None and cancel.is_set():
                _terminate(proc)
                clear_frame_dir(frame_dir, project.extraction.naming)
                raise OperationCancelled("Kare çıkarma")
    finally:
        if proc.stdout:
            proc.stdout.close()

    proc.wait()
    reader.join(timeout=_KILL_GRACE_S)
    tail = "\n".join(stderr_lines[-20:]).strip()

    if proc.returncode != 0:
        raise FrameExtractionFailed(
            t(
                "Kare çıkarma başarısız oldu. Video dosyası bozuk olabilir veya "
                "diskte yer kalmamış olabilir."
            ),
            detail=tail,
        )
    return tail


def _drain(stream: Iterable[str] | None, sink: list[str]) -> None:
    """ffmpeg stderr'ini arka planda toplar (boru dolup kilitlenmesin diye)."""
    if stream is None:
        return
    for line in stream:
        line = line.rstrip()
        if line:
            sink.append(line)
            log.debug("ffmpeg: %s", line)


def _terminate(proc: subprocess.Popen) -> None:
    """ffmpeg'i önce nazikçe, sonra zorla sonlandırır."""
    proc.terminate()
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:  # pragma: no cover - nadir
        proc.kill()
        proc.wait()
