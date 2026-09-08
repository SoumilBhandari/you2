"""Prosody: arousal for free.

The design doc treats ASR output as the input to memory. It isn't. How
something was said carries encoding signal the transcript throws away, and
the microphone array is already producing it.

Arousal is a direct multiplier on encoding strength, which is emotional
modulation of consolidation -- the mechanism by which the things you felt
strongly about are the things you still remember. A memory device with a
microphone that ignores prosody is discarding the cheapest salience signal
available to it.

Two honest caveats, both in docs/brain.md section 5.1:

  * Prosodic *valence* is weak. Arousal is largely energy and pitch variance
    and is robust; valence from acoustics alone is not, so text carries most
    of the valence estimate.
  * This is speaker-relative. It means nothing without a running baseline, so
    the first week of use is calibration and the estimates should be treated
    as noise until `n` is reasonable.

Seven floats per turn. Not audio, and not reconstructible as audio.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from ..clock import DAY
from ..substrate.store import Store

FEATURES = ("rms_mean", "rms_var", "f0_mean", "f0_var", "speech_rate",
            "pause_ratio", "onset_latency")

#: Human speech F0 bounds. Outside this the autocorrelation peak is noise.
F0_MIN, F0_MAX = 60.0, 400.0


@dataclass(slots=True)
class Prosody:
    rms_mean: float = 0.0
    rms_var: float = 0.0
    f0_mean: float = 0.0
    f0_var: float = 0.0
    speech_rate: float = 0.0
    pause_ratio: float = 0.0
    onset_latency: float = 0.0
    voiced_fraction: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def extract(samples: np.ndarray, sample_rate: int = 16000, *,
            onset_latency: float = 0.0, frame_ms: float = 30.0) -> Prosody:
    """Frame-level features from one turn of audio.

    Cheap by construction: RMS is a reduction, F0 is autocorrelation over
    30 ms frames, and both run on the frames the VAD has already produced.
    """
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return Prosody(onset_latency=onset_latency)
    n = max(1, int(sample_rate * frame_ms / 1000.0))
    n_frames = x.size // n
    if n_frames == 0:
        return Prosody(onset_latency=onset_latency)

    # Energy is measured **before** normalisation, and pitch after it.
    #
    # Peak-normalising the whole turn first made `rms_mean` a crest factor
    # carrying no loudness at all: across a 47x amplitude range (0.02 to
    # 0.95) it returned 0.5196 every time. That is the 0.35-weighted term in
    # `Baseline.arousal`, the largest single weight, so the arousal estimate
    # — and through it salience and initial storage strength — could not tell
    # a shout from a murmur. It contradicted this module's own headline and
    # brain.md §5.1's "RMS energy -> emphasis, agitation".
    #
    # Normalisation still earns its place for the autocorrelation below,
    # which wants a consistent scale and does not care about absolute level.
    absolute = x[: n_frames * n].reshape(n_frames, n)
    rms = np.sqrt(np.mean(absolute ** 2, axis=1) + 1e-12)

    peak = float(np.max(np.abs(x)))
    if peak > 0.0:
        x = x / peak
    frames = x[: n_frames * n].reshape(n_frames, n)
    # Which frames are speech, how fast, and at what pitch are all questions
    # about *shape*, and must stay scale-invariant — a quiet recording still
    # has voiced frames. Only the energy the arousal estimate reads is
    # absolute. Applying the 0.02 voicing floor to raw amplitude instead made
    # a whisper come back with no pitch and a pause ratio of 1.0.
    shape = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    voiced = shape > max(0.02, float(np.median(shape)) * 0.5)
    voiced_fraction = float(voiced.mean())

    f0s: list[float] = []
    lo = int(sample_rate / F0_MAX)
    hi = min(n - 1, int(sample_rate / F0_MIN))
    for i in np.nonzero(voiced)[0]:
        frame = frames[i] - frames[i].mean()
        if hi <= lo:
            break
        corr = np.correlate(frame, frame, mode="full")[frame.size - 1:]
        if corr[0] <= 0:
            continue
        segment = corr[lo:hi]
        if segment.size == 0:
            continue
        lag = int(np.argmax(segment)) + lo
        # A weak peak means the frame is unvoiced; taking it anyway is where
        # naive F0 tracking produces garbage variance.
        if corr[lag] < 0.3 * corr[0]:
            continue
        f0s.append(sample_rate / lag)

    duration = x.size / sample_rate
    # Syllable-nucleus proxy: energy peaks per second. Crude, and correlated
    # enough with speaking rate to be useful as a relative signal.
    smooth = np.convolve(shape, np.ones(3) / 3.0, mode="same")
    peaks = int(np.sum((smooth[1:-1] > smooth[:-2]) & (smooth[1:-1] > smooth[2:])
                       & (smooth[1:-1] > float(np.median(smooth)))))

    return Prosody(
        rms_mean=float(rms[voiced].mean()) if voiced.any() else float(rms.mean()),
        rms_var=float(rms.var()),
        f0_mean=float(np.mean(f0s)) if f0s else 0.0,
        f0_var=float(np.var(f0s)) if len(f0s) > 1 else 0.0,
        speech_rate=peaks / duration if duration > 0 else 0.0,
        pause_ratio=1.0 - voiced_fraction,
        onset_latency=onset_latency,
        voiced_fraction=voiced_fraction,
    )


class Baseline:
    """Running per-speaker mean and variance, exponentially weighted.

    Arousal is a z-score against this. Before roughly 30 turns the estimate
    should be treated as unreliable, and `confidence` says so rather than
    quietly returning a confident number from three samples.
    """

    def __init__(self, store: Store, speaker: str = "user",
                 halflife: float | None = None) -> None:
        self.store = store
        self.speaker = speaker
        self.halflife = halflife if halflife is not None else store.cfg.prosody_baseline_halflife
        self._cache: dict[str, tuple[float, float, int, float]] = {}
        self._load()

    def _load(self) -> None:
        rows = self.store.conn.execute(
            "SELECT feature, mean, var, n, updated_at FROM prosody_baseline "
            "WHERE speaker=?", (self.speaker,)).fetchall()
        for r in rows:
            self._cache[r["feature"]] = (r["mean"], r["var"], r["n"], r["updated_at"])

    @property
    def n(self) -> float:
        return max((v[2] for v in self._cache.values()), default=0.0)

    @property
    def confidence(self) -> float:
        """Ramps to 1.0 over ~30 observations. Multiplied into arousal so a
        cold baseline cannot produce a strong salience signal."""
        return min(1.0, self.n / 30.0)

    def update(self, p: Prosody, now: float) -> None:
        values = p.as_dict()
        rows = []
        for feature in FEATURES:
            x = float(values.get(feature, 0.0))
            mean, var, n, updated = self._cache.get(feature, (x, 0.0, 0, now))
            # Exponential forgetting: a voice changes, and a baseline built
            # from two years ago is not the right comparison for today.
            age = max(0.0, now - updated)
            decay = 0.5 ** (age / self.halflife) if self.halflife > 0 else 1.0
            n_eff = n * decay
            alpha = 1.0 / (n_eff + 1.0)
            new_mean = mean + alpha * (x - mean)
            new_var = (1 - alpha) * (var + alpha * (x - mean) ** 2)
            # Kept as a float. Truncating to int here means that with any
            # halflife longer than the sampling interval, n_eff comes back as
            # 0.9999 every time, int() floors it to 0, and the count never
            # grows past 1 -- so confidence stays pinned near zero forever and
            # prosody silently contributes nothing.
            n_new = n_eff + 1.0
            self._cache[feature] = (new_mean, new_var, n_new, now)
            rows.append((self.speaker, feature, new_mean, new_var, n_new, now))
        self.store.conn.executemany(
            "INSERT INTO prosody_baseline(speaker, feature, mean, var, n, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(speaker, feature) DO UPDATE SET "
            "mean=excluded.mean, var=excluded.var, n=excluded.n, "
            "updated_at=excluded.updated_at", rows)

    def z(self, feature: str, value: float) -> float:
        entry = self._cache.get(feature)
        if entry is None or entry[2] < 2:
            return 0.0
        mean, var, _n, _t = entry
        sd = math.sqrt(max(var, 1e-9))
        if sd < 1e-6:
            return 0.0
        return (value - mean) / sd

    def arousal(self, p: Prosody) -> float:
        """Weighted z-score over the features that actually track arousal.

        Energy and pitch variance dominate; speaking rate contributes; pause
        ratio is inverted, because dense speech with few pauses reads as
        agitated and long pauses read as flat or word-finding.
        """
        z = (0.35 * self.z("rms_mean", p.rms_mean)
             + 0.20 * self.z("f0_var", p.f0_var)
             + 0.20 * self.z("f0_mean", p.f0_mean)
             + 0.15 * self.z("speech_rate", p.speech_rate)
             - 0.10 * self.z("pause_ratio", p.pause_ratio))
        # Logistic to [0, 1], centred so a typical turn sits at 0.5.
        raw = 1.0 / (1.0 + math.exp(-z))
        return 0.5 + (raw - 0.5) * self.confidence


def text_arousal(text: str) -> float:
    """Fallback when there is no audio -- which is every path in the eval
    harness and every imported document.

    Deliberately crude. It exists so that the salience gate degrades to
    something sane rather than to a constant.
    """
    if not text:
        return 0.5
    stripped = text.strip()
    score = 0.5
    score += 0.10 * min(2, stripped.count("!"))
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) > 8:
        caps = sum(1 for c in letters if c.isupper()) / len(letters)
        if caps > 0.4:
            score += 0.15
    low = stripped.lower()
    for word in ("really", "very", "so ", "extremely", "completely", "absolutely",
                 "never", "always", "hate", "love", "furious", "thrilled",
                 "terrible", "amazing", "exhausted", "wiped", "desperate"):
        if word in low:
            score += 0.05
    if low.endswith("...") or " um " in low or " uh " in low:
        score -= 0.05
    return max(0.0, min(1.0, score))
