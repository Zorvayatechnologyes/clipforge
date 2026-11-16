"""Cheap, dependency-light audio analysis.

Streams have a simple tell: the loud, fast, wildly modulated stretches are where
people react. This module turns a WAV into a small set of per-window numbers
(loudness, dynamics, burst rate, onset flux) that the ranker can score.
It is a *proxy* for excitement, not real laughter detection -- but on gaming,
podcast and IRL streams it lines up with the funny bits remarkably well, and it
costs a couple of seconds instead of a GPU.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

CHUNK_FRAMES = 1 << 18  # ~262k samples per read


@dataclass
class AudioTrack:
    """Envelope features sampled on a fixed grid."""

    t: np.ndarray          # centre time of each frame, seconds
    rms: np.ndarray        # 0..1 normalised loudness
    flux: np.ndarray       # 0..1 positive onset strength
    peaks: np.ndarray      # 1.0 where the envelope has a local maximum
    duration: float

    def slice(self, start: float, end: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        i0 = int(np.searchsorted(self.t, start, side="left"))
        i1 = int(np.searchsorted(self.t, end, side="right"))
        return self.rms[i0:i1], self.flux[i0:i1], self.peaks[i0:i1]


def _read_mono(path: Path, chunk_frames: int = CHUNK_FRAMES):
    """Yield float32 mono chunks plus the sample rate (16-bit PCM assumed)."""
    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        if width != 2:
            raise ValueError(f"expected 16-bit audio, got {width * 8}-bit")
        while True:
            raw = handle.readframes(chunk_frames)
            if not raw:
                break
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            if channels > 1:
                data = data.reshape(-1, channels).mean(axis=1)
            yield data, sample_rate


def analyse(path: str | Path, frame_sec: float = 0.05, hop_sec: float = 0.025) -> AudioTrack:
    """Build an :class:`AudioTrack` from a mono WAV file, in constant memory."""
    path = Path(path)
    rms_parts: list[np.ndarray] = []
    sample_rate = 16000
    frame = hop = 1
    leftover = np.empty(0, dtype=np.float32)
    started = False

    for chunk, sample_rate in _read_mono(path):
        if not started:
            frame = max(2, int(round(sample_rate * frame_sec)))
            hop = max(1, int(round(sample_rate * hop_sec)))
            started = True
        buf = np.concatenate((leftover, chunk)) if leftover.size else chunk
        if buf.size < frame:
            leftover = buf
            continue
        windows = np.lib.stride_tricks.sliding_window_view(buf, frame)[::hop]
        count = max(0, (buf.size - frame) // hop + 1)
        windows = windows[:count]
        if count:
            # mean(x^2) over each window, in one vectorised pass
            rms_parts.append(np.sqrt(np.mean(windows * windows, axis=1)))
        consumed = (count - 1) * hop + frame if count else 0
        leftover = buf[consumed:]

    if not rms_parts:
        raise ValueError("audio track is too short to analyse")

    rms_raw = np.concatenate(rms_parts).astype(np.float32)
    hop_time = hop / float(sample_rate)
    t = np.arange(rms_raw.size, dtype=np.float64) * hop_time + frame_sec / 2.0

    # Loudness -> dB, then robust 10/90 normalisation so one quiet mic
    # or one clipping streamer does not skew every score.
    db = 20.0 * np.log10(np.maximum(rms_raw, 1e-7))
    lo, hi = np.percentile(db, [10.0, 90.0])
    span = max(float(hi - lo), 1e-6)
    norm = np.clip((db - lo) / span, 0.0, 1.0)

    # Smooth the envelope a little; raw frame RMS is jittery.
    kernel = np.ones(5, dtype=np.float32) / 5.0
    smooth = np.convolve(norm, kernel, mode="same")

    diff = np.diff(smooth, prepend=smooth[0])
    flux = np.clip(diff, 0.0, None)
    flux = flux / max(float(np.percentile(flux, 95.0)), 1e-6)
    flux = np.clip(flux, 0.0, 1.0)

    # Local maxima above the noise floor = individual bursts of sound.
    peaks = np.zeros_like(smooth)
    if smooth.size >= 3:
        mid = smooth[1:-1]
        is_peak = (mid > smooth[:-2]) & (mid >= smooth[2:]) & (mid > 0.45)
        peaks[1:-1] = is_peak.astype(np.float32)

    return AudioTrack(
        t=t,
        rms=smooth,
        flux=flux,
        peaks=peaks,
        duration=float(t[-1]) if t.size else 0.0,
    )


def window_features(track: AudioTrack, start: float, end: float) -> dict[str, float]:
    """Summarise one time window into 0..1 features."""
    rms, flux, peaks = track.slice(start, end)
    if rms.size == 0:
        return {"energy": 0.0, "dynamics": 0.0, "bursts": 0.0, "flux": 0.0, "coverage": 0.0}

    duration = max(end - start, 1e-6)
    energy = float(np.mean(rms))
    dynamics = float(np.std(rms))
    bursts = float(np.sum(peaks)) / duration
    onset = float(np.mean(flux))

    return {
        "energy": float(np.clip(energy, 0.0, 1.0)),
        # 0.25 std is already a very lively stretch
        "dynamics": float(np.clip(dynamics / 0.25, 0.0, 1.0)),
        # 1.5 bursts/second is frantic; 0 is flat
        "bursts": float(np.clip(bursts / 1.5, 0.0, 1.0)),
        "flux": float(np.clip(onset, 0.0, 1.0)),
        # how much of the window is not dead air
        "coverage": float(np.clip(np.mean(rms > 0.15), 0.0, 1.0)),
    }


def quiet_points(track: AudioTrack, start: float, end: float, limit: int = 40) -> list[float]:
    """Times inside [start, end] where the audio dips -- good cut points."""
    i0 = int(np.searchsorted(track.t, start, side="left"))
    i1 = int(np.searchsorted(track.t, end, side="right"))
    seg = track.rms[i0:i1]
    times = track.t[i0:i1]
    if seg.size < 3:
        return []
    mid = seg[1:-1]
    dips = np.where((mid < seg[:-2]) & (mid <= seg[2:]))[0] + 1
    if dips.size == 0:
        return []
    best = dips[np.argsort(seg[dips])][:limit]
    return sorted(float(times[i]) for i in best)
