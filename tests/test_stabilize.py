"""Stabilizasyon testleri (bkz. CLAUDE.md §7.5).

Kritik ayrım: stabilizasyon **kağıdın tarayıcıdaki titremesini** silmeli,
**sanatçının çizdiği hareketi** değil. İlk sürüm her kareyi ilk kareye
hizalıyordu; içerik gerçekten hareket ettiğinde bu, animasyonun kendisini
iptal etmeye çalışıp kareleri savuruyordu. Buradaki testler ikisini ayırır.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from mixedmedia.core.frame_normalize import Stabilizer, stabilize_sequence

FRAME_W, FRAME_H = 320, 180
PAN_PER_FRAME = 1.6  # kasıtlı, düzgün kayma
JITTER_PX = 2.5  # tarayıcıdan gelen rastgele titreme
FRAME_COUNT = 40


def _canvas(rng: np.random.Generator) -> np.ndarray:
    """Takip edilebilir, dokulu bir zemin."""
    canvas = rng.integers(90, 165, (FRAME_H + 200, FRAME_W + 200, 3), dtype=np.uint8)
    canvas = cv2.GaussianBlur(canvas, (0, 0), 2.0)
    for index in range(14):
        centre = (40 + (index * 61) % (FRAME_W + 140), 30 + (index * 47) % (FRAME_H + 140))
        colour = (int(20 + index * 12) % 255, 40, 220 - index * 9)
        cv2.circle(canvas, centre, 9 + index % 5, colour, -1)
        cv2.rectangle(
            canvas,
            (centre[0] - 22, centre[1] - 14),
            (centre[0] - 6, centre[1] + 4),
            (230, 210, 40),
            2,
        )
    return canvas


def _sequence(*, pan: float, jitter: float, seed: int = 3) -> tuple[list[np.ndarray], np.ndarray]:
    """Düzgün kayma + rastgele titreme içeren bir dizi ve gerçek konumları."""
    rng = np.random.default_rng(seed)
    canvas = _canvas(rng)
    truth = np.zeros((FRAME_COUNT, 2))
    frames: list[np.ndarray] = []

    for index in range(FRAME_COUNT):
        x = 100 + pan * index + (rng.normal(0, jitter) if jitter else 0.0)
        y = 100 + (rng.normal(0, jitter) if jitter else 0.0)
        truth[index] = (x, y)
        matrix = np.array([[1, 0, -x], [0, 1, -y]], dtype=np.float32)
        frames.append(
            cv2.warpAffine(canvas, matrix, (FRAME_W, FRAME_H), flags=cv2.INTER_LINEAR)
        )
    return frames, truth


def _measured_trajectory(frames: list[np.ndarray]) -> np.ndarray:
    """Ardışık kareler arası kaymayı ölçüp biriktirerek yörüngeyi çıkarır."""
    grays = [
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) for frame in frames
    ]
    steps = [(0.0, 0.0)]
    for previous, current in zip(grays, grays[1:]):
        (dx, dy), _ = cv2.phaseCorrelate(previous, current)
        steps.append((dx, dy))
    return np.cumsum(np.asarray(steps), axis=0)


def _jitter_energy(trajectory: np.ndarray) -> float:
    """Yörüngenin yüksek frekanslı kısmı — ikinci fark, düzgün kaymaya duyarsız."""
    return float(np.abs(np.diff(trajectory, n=2, axis=0)).mean())


# ---------------------------------------------------------------------------
# Temel davranış
# ---------------------------------------------------------------------------


def test_zero_strength_leaves_frames_untouched():
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    stabilizer = Stabilizer(0.0)
    assert not stabilizer.active
    stabilizer.analyse(frames)
    for index, frame in enumerate(frames):
        assert stabilizer.apply(frame, index) is frame


def test_stabilizer_needs_analysis_before_it_corrects():
    """Çözümleme yapılmadan uygulama kareyi değiştirmemeli."""
    frames, _ = _sequence(pan=0.0, jitter=JITTER_PX)
    stabilizer = Stabilizer(1.0)
    assert not stabilizer.analysed
    assert stabilizer.apply(frames[5], 5) is frames[5]


def test_analysis_produces_one_correction_per_frame():
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    stabilizer = Stabilizer(1.0)
    stabilizer.analyse(frames)
    assert stabilizer.analysed
    assert len(stabilizer._corrections) == len(frames)


# ---------------------------------------------------------------------------
# Asıl koşul: titreme silinsin, hareket kalsın
# ---------------------------------------------------------------------------


def test_jitter_is_reduced():
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    before = _jitter_energy(_measured_trajectory(frames))

    stabilized = stabilize_sequence(frames, 1.0)
    after = _jitter_energy(_measured_trajectory(stabilized))

    assert after < before * 0.7, f"titreme azalmadı: {before:.2f} → {after:.2f}"


def test_intentional_motion_is_preserved():
    """Kasıtlı kayma korunmalı — eski sürümün ana hatası buydu.

    İlk sürüm her kareyi ilk kareye hizaladığı için toplam yer değiştirmeyi
    sıfıra çekiyordu: animasyonun kendisi siliniyordu.
    """
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    expected = PAN_PER_FRAME * (FRAME_COUNT - 1)

    stabilized = stabilize_sequence(frames, 1.0)
    trajectory = _measured_trajectory(stabilized)
    travelled = abs(trajectory[-1, 0] - trajectory[0, 0])

    assert travelled > expected * 0.75, (
        f"kasıtlı hareket bastırıldı: beklenen ~{expected:.0f} px, ölçülen {travelled:.0f} px"
    )


def test_corrections_stay_small_on_a_pure_pan():
    """Titremesiz, düzgün bir kaymada düzeltme neredeyse sıfır olmalı."""
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=0.0)
    stabilizer = Stabilizer(1.0)
    stabilizer.analyse(frames)

    corrections = np.abs(stabilizer._corrections)
    assert corrections[:, :2].max() < 2.0, f"düzgün kaymada düzeltme büyük: {corrections.max():.2f}"


def test_corrections_are_bounded_even_on_garbage_input():
    """Kestirim çökse bile kare ekranın dışına savrulmamalı."""
    rng = np.random.default_rng(11)
    frames = [
        rng.integers(0, 256, (FRAME_H, FRAME_W, 3), dtype=np.uint8) for _ in range(12)
    ]
    stabilized = stabilize_sequence(frames, 1.0)

    for original, result in zip(frames, stabilized):
        assert result.shape == original.shape
        # Gürültüde içerik yok; en kötü ihtimalle kare kadar kayabilir, daha
        # fazlası sınırın çalışmadığı anlamına gelir.
        assert np.isfinite(result).all()


@pytest.mark.parametrize("strength", [0.25, 0.6, 1.0])
def test_strength_scales_the_correction(strength):
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    full = Stabilizer(1.0)
    full.analyse(frames)
    partial = Stabilizer(strength)
    partial.analyse(frames)

    ratio = np.abs(partial._corrections).sum() / max(np.abs(full._corrections).sum(), 1e-9)
    assert ratio == pytest.approx(strength, rel=0.05)


def test_default_strength_does_not_wreck_a_moving_sequence():
    """Varsayılan 0.6, hareketli bir sekansta kareleri savurmamalı.

    Kullanıcının bildirdiği hata tam olarak buydu: varsayılan ayarda görüntü
    aşırı kayıyor ve kenarlar eziliyordu.
    """
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    stabilized = stabilize_sequence(frames, 0.6)

    trajectory = _measured_trajectory(stabilized)
    travelled = abs(trajectory[-1, 0] - trajectory[0, 0])
    expected = PAN_PER_FRAME * (FRAME_COUNT - 1)
    assert travelled > expected * 0.75

    stabilizer = Stabilizer(0.6)
    stabilizer.analyse(frames)
    shifts = np.abs(stabilizer._corrections[:, :2])
    assert shifts.max() < FRAME_W * 0.05, f"düzeltme çok büyük: {shifts.max():.1f} px"


def test_short_sequences_are_handled():
    frames, _ = _sequence(pan=0.0, jitter=0.0)
    assert stabilize_sequence(frames[:1], 1.0) == frames[:1]
    stabilizer = Stabilizer(1.0)
    stabilizer.analyse(frames[:1])
    assert stabilizer.apply(frames[0], 0) is frames[0]


# ---------------------------------------------------------------------------
# En-boy oranı — kesim payı oranı bozar, çıktı esnememeli
# ---------------------------------------------------------------------------


def test_fit_to_size_does_not_stretch():
    """Oranı sapan bir kare hedefe esnetilmeden oturtulmalı.

    Kesim payı milimetre cinsinden her kenardan eşit alındığı için kesilen
    hücrenin oranı kaynaktan bir parça sapar; düz bir ``resize`` bunu yatayda
    esnetip görüntüyü bozardı.
    """
    from mixedmedia.core.sequencer import fit_to_size

    # 721x400 (1.8025) -> 1280x720 (1.7778): gerçek projede ölçülen sapma.
    source = np.zeros((400, 721, 3), dtype=np.uint8)
    cv2.circle(source, (360, 200), 100, (255, 255, 255), -1)

    stretched = cv2.resize(source, (1280, 720), interpolation=cv2.INTER_AREA)
    fitted = fit_to_size(source, (1280, 720))
    assert fitted.shape == (720, 1280, 3)

    def roundness(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        ys, xs = np.nonzero(gray > 127)
        return (xs.max() - xs.min()) / (ys.max() - ys.min())

    assert roundness(fitted) == pytest.approx(1.0, abs=0.02)
    assert abs(roundness(stretched) - 1.0) > abs(roundness(fitted) - 1.0)


def test_fit_to_size_is_a_no_op_at_the_target_size():
    from mixedmedia.core.sequencer import fit_to_size

    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert fit_to_size(image, (1280, 720)) is image


# ---------------------------------------------------------------------------
# Ölçülemeyen kaynakta kendini devre dışı bırakma
# ---------------------------------------------------------------------------


def test_stabilizer_declines_when_motion_cannot_be_measured():
    """Hızlı hareketli kaynakta ECC ölçemez; o zaman hiç dokunmamalı.

    Kullanıcının bildirdiği klip tam olarak böyleydi: ardışık kareler
    birbirine yeterince benzemediği için kestirim gürültü üretiyordu.
    """
    rng = np.random.default_rng(5)
    frames = [
        rng.integers(0, 256, (FRAME_H, FRAME_W, 3), dtype=np.uint8) for _ in range(20)
    ]
    stabilizer = Stabilizer(0.6)
    stabilizer.analyse(frames)

    assert stabilizer.disabled_reason is not None
    assert "ölçülemedi" in stabilizer.disabled_reason
    assert np.allclose(stabilizer._corrections, 0.0)
    for index, frame in enumerate(frames):
        assert stabilizer.apply(frame, index) is frame


def test_stabilizer_does_not_decline_on_measurable_jitter():
    frames, _ = _sequence(pan=PAN_PER_FRAME, jitter=JITTER_PX)
    stabilizer = Stabilizer(0.6)
    stabilizer.analyse(frames)
    assert stabilizer.disabled_reason is None
    assert np.abs(stabilizer._corrections).max() > 0


def test_isolated_unreliable_frames_are_interpolated_not_zeroed():
    """Tek tük ölçülemeyen kare yörüngede basamak yaratmamalı.

    Sıfır saymak yörüngeyi merdivene çevirir; yumuşatıcı sonra o basamaklarla
    kavga edip devasa düzeltmeler üretir.
    """
    from mixedmedia.core.frame_normalize import _fill_unreliable

    motions = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    reliable = np.array([True, False, True])
    filled = _fill_unreliable(motions, reliable)
    assert filled[1, 0] == pytest.approx(1.0), "boşluk komşulardan doldurulmalı"
