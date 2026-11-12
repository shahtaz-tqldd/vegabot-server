"""WAV handling for custom voice recordings.

Gemini's replicated voices take 16-bit signed little-endian WAV at 24 kHz
mono, so every upload is normalized to that format: any PCM WAV
(8/16/24/32-bit, mono or stereo, any sample rate) is accepted and
converted with numpy.
"""

import io
import wave

import numpy as np

TARGET_SAMPLE_RATE = 24000
TARGET_SAMPLE_WIDTH = 2  # bytes: 16-bit
WAV_MIME_TYPE = "audio/wav"


class VoiceAudioError(ValueError):
    """The recording is not a usable WAV file."""


def _to_float_samples(raw, sample_width):
    if sample_width == 1:  # unsigned 8-bit
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    if sample_width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    if sample_width == 3:  # packed signed 24-bit
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = data[:, 0] | (data[:, 1] << 8) | (data[:, 2] << 16)
        values = np.where(values >= 1 << 23, values - (1 << 24), values)
        return values.astype(np.float32) / (1 << 23)
    if sample_width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float32) / (1 << 31)
    raise VoiceAudioError("Unsupported WAV sample width.")


def _encode_wav(samples):
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(TARGET_SAMPLE_WIDTH)
        wav.setframerate(TARGET_SAMPLE_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def normalize_wav(file_obj):
    """Return ``(wav_bytes, duration_seconds)`` as 24 kHz mono 16-bit WAV."""
    try:
        file_obj.seek(0)
        with wave.open(file_obj, "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise VoiceAudioError(
            "Upload a PCM WAV recording (for example 16-bit, 24 kHz, mono)."
        ) from exc

    if not channels or not sample_rate:
        raise VoiceAudioError("The WAV file has no audio.")

    samples = _to_float_samples(raw, sample_width)
    frame_count = len(samples) // channels
    if frame_count == 0:
        raise VoiceAudioError("The WAV file has no audio.")
    samples = samples[: frame_count * channels].reshape(-1, channels).mean(axis=1)

    if sample_rate != TARGET_SAMPLE_RATE:
        duration = frame_count / sample_rate
        target_count = max(1, int(round(duration * TARGET_SAMPLE_RATE)))
        samples = np.interp(
            np.linspace(0, frame_count - 1, target_count),
            np.arange(frame_count),
            samples,
        ).astype(np.float32)

    if not np.any(np.abs(samples) > 1e-3):
        raise VoiceAudioError("The recording is silent.")

    return _encode_wav(samples), len(samples) / TARGET_SAMPLE_RATE


def clip_wav(wav_bytes, max_seconds):
    """The first ``max_seconds`` of a normalized WAV, as WAV bytes."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        frames = min(wav.getnframes(), int(max_seconds * wav.getframerate()))
        params = wav.getparams()
        raw = wav.readframes(frames)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setparams(params)
        out.writeframes(raw)
    return buffer.getvalue()


def pcm_to_wav(pcm, *, sample_rate=TARGET_SAMPLE_RATE):
    """Wrap raw 16-bit mono PCM (e.g. Live output audio) in a WAV header."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(TARGET_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
