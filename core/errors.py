"""Uygulamaya özel hata sınıfları.

Her hata, kullanıcının anlayacağı bir Türkçe mesaj taşır (bkz. CLAUDE.md §12).
UI ve CLI bu mesajı doğrudan gösterir; teknik ayrıntı `detail` alanındadır.
"""

from __future__ import annotations

from .i18n import t


class MixedMediaError(Exception):
    """Tüm uygulama hatalarının atası."""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - önemsiz
        return self.message if not self.detail else f"{self.message}\n({self.detail})"


class FFmpegNotFound(MixedMediaError):
    """ffmpeg veya ffprobe sistemde bulunamadı."""

    def __init__(self, binary: str) -> None:
        super().__init__(
            t(
                "'{binary}' bulunamadı. Video işlemleri için FFmpeg kurulu olmalı.\n"
                "Kurulum: https://ffmpeg.org/download.html "
                "(Windows'ta 'winget install Gyan.FFmpeg' de çalışır). "
                "Kurduktan sonra ffmpeg klasörünü PATH'e ekleyin.",
                binary=binary,
            )
        )
        self.binary = binary


class VideoProbeFailed(MixedMediaError):
    """ffprobe videoyu okuyamadı."""


class FrameExtractionFailed(MixedMediaError):
    """Kare çıkarma sırasında ffmpeg hata verdi."""


class EncodingFailed(MixedMediaError):
    """Video kodlama sırasında ffmpeg hata verdi."""


class LayoutInvalid(MixedMediaError):
    """Verilen yerleşim parametreleriyle geçerli bir ızgara kurulamıyor."""


class ProjectNotFound(MixedMediaError):
    """Proje klasörü veya project.mmp.json bulunamadı."""


class ProjectSchemaMismatch(MixedMediaError):
    """Proje dosyasının schema_version'ı bu sürümle uyumsuz."""

    def __init__(self, found: int, expected: int) -> None:
        super().__init__(
            t(
                "Proje dosyası sürüm {found} ile yazılmış, bu uygulama sürüm "
                "{expected} bekliyor. Projeyi bu sürümle yeniden oluşturun veya "
                "uygulamayı güncelleyin.",
                found=found,
                expected=expected,
            )
        )
        self.found = found
        self.expected = expected


class MarkerDetectionFailed(MixedMediaError):
    """Taranan sayfada fiducial'lar bulunamadı."""


class OperationCancelled(MixedMediaError):
    """Kullanıcı işlemi iptal etti."""

    def __init__(self, what: str = "İşlem") -> None:
        # ``what`` çağrı yerinden Türkçe gelir; anahtar olarak kullanılır.
        super().__init__(
            t("{what} kullanıcı tarafından iptal edildi.", what=t(what))
        )
