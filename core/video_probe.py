"""ffprobe sarmalayıcısı ve FFmpeg ikili dosya keşfi (bkz. CLAUDE.md §6.1).

Wrapper kütüphanesi kullanılmaz; komutlar ``subprocess`` ile açıkça kurulur.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from .constants import SHA256_CHUNK_BYTES
from .errors import FFmpegNotFound, VideoProbeFailed
from .i18n import t
from .resources import search_dirs
from .models import SourceInfo

log = logging.getLogger(__name__)

_PROBE_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# İkili dosya keşfi
# ---------------------------------------------------------------------------


def find_binary(name: str) -> str | None:
    """FFmpeg ikilisini arar.

    Sıra: ``MM_FFMPEG_DIR`` → uygulamanın yanı (``ffmpeg/``, ``ffmpeg/bin/``)
    → PATH. Uygulamanın yanına bakmak paketlenmiş sürüm için önemlidir:
    kullanıcı FFmpeg'i kurmak yerine klasöre bırakabilsin diye.

    FFmpeg pakete **gömülmez**: lisansı dağıtımda ek yükümlülük getirir ve
    derlemeyi onlarca megabayt şişirir.
    """
    filename = name + (".exe" if os.name == "nt" else "")

    override = os.environ.get("MM_FFMPEG_DIR")
    if override:
        candidate = Path(override) / filename
        if candidate.is_file():
            return str(candidate)

    for directory in search_dirs("ffmpeg/bin", "ffmpeg", ""):
        candidate = directory / filename
        if candidate.is_file():
            return str(candidate)

    return shutil.which(name)


def require_ffmpeg() -> str:
    """ffmpeg yolunu döner; yoksa kurulum yönergeli hata fırlatır."""
    path = find_binary("ffmpeg")
    if not path:
        raise FFmpegNotFound("ffmpeg")
    return path


def require_ffprobe() -> str:
    """ffprobe yolunu döner; yoksa kurulum yönergeli hata fırlatır."""
    path = find_binary("ffprobe")
    if not path:
        raise FFmpegNotFound("ffprobe")
    return path


def ffmpeg_available() -> tuple[bool, str]:
    """Açılışta kullanılan hızlı kontrol: (uygun_mu, açıklama)."""
    ffmpeg, ffprobe = find_binary("ffmpeg"), find_binary("ffprobe")
    if ffmpeg and ffprobe:
        return True, f"ffmpeg: {ffmpeg}"
    missing = ", ".join(n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p)
    return False, f"Eksik: {missing}"


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _parse_rate(value: str | None) -> float | None:
    """``"30000/1001"`` biçimindeki kesirli kare hızını float'a çevirir."""
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def _extract_rotation(stream: dict[str, Any]) -> int:
    """Stream'in görüntüleme dönüşünü 0/90/180/270 olarak döner.

    Dikey çekilmiş telefon videolarında bu metadata mutlaka uygulanmalıdır,
    yoksa kareler yan yatar (CLAUDE.md §6.1).
    """
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            # Display Matrix dönüşü ters işaretlidir.
            return int(round(-float(side_data["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(round(float(tag))) % 360
        except ValueError:
            log.warning("Okunamayan rotate etiketi: %r", tag)
    return 0


def sha256_file(path: Path, chunk_bytes: int = SHA256_CHUNK_BYTES) -> str:
    """Dosyanın SHA-256 özetini akış halinde hesaplar (belleğe tamamı alınmaz)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------


def run_ffprobe(path: Path) -> dict[str, Any]:
    """Ham ffprobe JSON çıktısını döner."""
    if not path.is_file():
        raise VideoProbeFailed(t("Video dosyası bulunamadı: {path}", path=path))

    cmd = [
        require_ffprobe(),
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    log.debug("ffprobe komutu: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=_PROBE_TIMEOUT_S
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProbeFailed(
            t("ffprobe zaman aşımına uğradı: {name}", name=path.name)
        ) from exc

    if proc.returncode != 0:
        raise VideoProbeFailed(
            t(
                "'{name}' okunamadı. Dosya bozuk veya desteklenmeyen bir "
                "formatta olabilir.",
                name=path.name,
            ),
            detail=proc.stderr.strip()[:2000],
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VideoProbeFailed(
            t("ffprobe çıktısı çözümlenemedi: {name}", name=path.name)
        ) from exc


def probe_video(path: Path, *, compute_hash: bool = True) -> SourceInfo:
    """Videoyu inceler ve ``SourceInfo`` üretir.

    Args:
        path: Video dosyası.
        compute_hash: SHA-256 hesaplansın mı (büyük dosyalarda kapatılabilir).

    Raises:
        FFmpegNotFound: ffprobe kurulu değilse.
        VideoProbeFailed: Dosya okunamıyorsa veya video akışı yoksa.
    """
    path = Path(path)
    data = run_ffprobe(path)

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoProbeFailed(
            t(
                "'{name}' içinde video akışı yok. Ses dosyası veya bozuk bir "
                "kayıt olabilir.",
                name=path.name,
            )
        )
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    fmt = data.get("format", {})
    duration = _first_float(video.get("duration"), fmt.get("duration"))
    if duration is None or duration <= 0:
        raise VideoProbeFailed(
            t(
                "'{name}' süresi okunamadı. Dosya eksik indirilmiş olabilir; "
                "videoyu yeniden kodlayıp deneyin.",
                name=path.name,
            )
        )

    fps = _parse_rate(video.get("avg_frame_rate")) or _parse_rate(video.get("r_frame_rate"))
    if fps is None:
        raise VideoProbeFailed(t("'{name}' kare hızı okunamadı.", name=path.name))

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width <= 0 or height <= 0:
        raise VideoProbeFailed(t("'{name}' çözünürlüğü okunamadı.", name=path.name))

    info = SourceInfo(
        path=str(path),
        sha256=sha256_file(path) if compute_hash else "",
        duration_s=duration,
        native_fps=fps,
        width=width,
        height=height,
        rotation=_extract_rotation(video),
        has_audio=has_audio,
    )
    log.info(
        "Probe: %s — %.2f sn, %.3f fps, %d×%d, rotation=%d, ses=%s",
        path.name,
        info.duration_s,
        info.native_fps,
        info.width,
        info.height,
        info.rotation,
        info.has_audio,
    )
    return info


def _first_float(*values: Any) -> float | None:
    for value in values:
        if value in (None, "", "N/A"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
