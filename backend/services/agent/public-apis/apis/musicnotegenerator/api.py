import base64
import math
import struct


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    note = params.get("note", "A4")
    duration_ms = params.get("duration_ms", 500)
    sample_rate = 44000
    freq_map = {"C": 261.63, "D": 293.66, "E": 329.63, "F": 349.23, "G": 392.00, "A": 440.00, "B": 493.88}
    try:
        name = str(note).strip().upper()
        letter = name[0] if name else "A"
        octave = int(name[1]) if len(name) > 1 and name[1].isdigit() else 4
        freq = freq_map.get(letter, 440) * (2 ** (octave - 4))
    except Exception:
        freq = 440.0
    try:
        duration_ms = int(duration_ms)
        duration_ms = max(10, min(5000, duration_ms))
    except (TypeError, ValueError):
        duration_ms = 500
    n_samples = int(sample_rate * duration_ms / 1000)
    buf = []
    for i in range(n_samples):
        t = i / sample_rate
        sample = 0.3 * math.sin(2 * math.pi * freq * t)
        buf.append(struct.pack("<h", int(sample * 32767)))
    wav = b"".join(buf)
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(wav), b"WAVE", b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16, b"data", len(wav))
    b64 = base64.b64encode(header + wav).decode("ascii")
    data = {"note": note, "frequency_hz": round(freq, 2), "duration_ms": duration_ms, "wav_base64": b64}
    return {"status": "ok", "error": None, "data": data}
