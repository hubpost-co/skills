import os
import wave

import numpy as np

RATE = 48000


def save(path, samples):
    samples = np.clip(samples, -1.0, 1.0)
    stereo = np.stack([samples, samples], axis=1)
    data = (stereo * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data)


def lowpass_sweep(noise, start_hz, end_hz):
    out = np.zeros_like(noise)
    cutoffs = np.geomspace(start_hz, end_hz, len(noise))
    y = 0.0
    for i, x in enumerate(noise):
        a = 1.0 - np.exp(-2.0 * np.pi * cutoffs[i] / RATE)
        y += a * (x - y)
        out[i] = y
    return out


def whoosh(seconds=0.45, seed=7):
    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    t = np.linspace(0.0, 1.0, n)
    noise = rng.standard_normal(n)
    rising = lowpass_sweep(noise, 300.0, 4500.0)
    falling = lowpass_sweep(noise[::-1], 4500.0, 600.0)
    peak = 0.62
    mix = np.where(t < peak, rising, falling)
    envelope = np.where(t < peak, (t / peak) ** 2.2, ((1.0 - t) / (1.0 - peak)) ** 1.6)
    sound = mix * envelope
    fade = np.minimum(1.0, np.minimum(t, 1.0 - t) * 60.0)
    sound *= fade
    return sound / np.max(np.abs(sound)) * 0.7


def pop(seconds=0.12):
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    freq = 900.0 * np.exp(-t * 28.0) + 220.0
    phase = 2.0 * np.pi * np.cumsum(freq) / RATE
    sound = np.sin(phase) * np.exp(-t * 38.0)
    sound[: int(RATE * 0.002)] *= np.linspace(0.0, 1.0, int(RATE * 0.002))
    return sound / np.max(np.abs(sound)) * 0.6


def main():
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sfx")
    os.makedirs(folder, exist_ok=True)
    save(os.path.join(folder, "whoosh.wav"), whoosh())
    save(os.path.join(folder, "pop.wav"), pop())
    print(f"wrote whoosh.wav and pop.wav to {os.path.abspath(folder)}")


if __name__ == "__main__":
    main()
