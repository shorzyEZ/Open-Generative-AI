#!/usr/bin/env python3
"""
flowfilm — генеративное кино без API, без GPU и без интернета.

Фильм рендерится из математики: сид -> векторное поле (fBm-шум) ->
тысячи частиц оставляют светящиеся следы -> тональная компрессия,
bloom, виньетка, зерно -> кадры уходят прямо в ffmpeg.
Звук синтезируется аддитивно (синусы + шум + LFO), тоже кодом.

Примеры:
    python3 generative/flowfilm.py --seed 7  --palette ember --out out/ember.mp4
    python3 generative/flowfilm.py --seed 21 --palette ink   --out out/ink.mp4 --poster out/ink.png
    python3 generative/flowfilm.py --seed 3  --palette neon  --seconds 6 --fps 24

Зависимости: numpy, pillow, ffmpeg (imageio-ffmpeg подойдёт).
"""
import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageFilter

PALETTES = {
    # имя: (тёмный фон, основные цвета трасс)
    "ember": ((10, 6, 4), [(255, 214, 140), (255, 138, 40), (196, 58, 12), (92, 22, 6)]),
    "ink":   ((4, 8, 14), [(210, 240, 255), (90, 190, 230), (30, 90, 170), (12, 30, 70)]),
    "neon":  ((6, 2, 10), [(255, 120, 240), (110, 220, 255), (150, 60, 255), (40, 10, 70)]),
    "moss":  ((5, 10, 6), [(226, 255, 190), (140, 220, 120), (40, 140, 90), (10, 50, 34)]),
    "bone":  ((12, 11, 10), [(255, 250, 240), (220, 205, 180), (150, 130, 105), (70, 58, 46)]),
}


# ---------------------------------------------------------------- шум

def _grid(size, rng):
    return rng.random((size + 1, size + 1)).astype(np.float32)


def value_noise(x, y, grid):
    """Билинейный value-noise на сетке grid (fBm собирается снаружи)."""
    gs = grid.shape[0] - 1
    xi = np.floor(x).astype(np.int32) % gs
    yi = np.floor(y).astype(np.int32) % gs
    xf = (x - np.floor(x)).astype(np.float32)
    yf = (y - np.floor(y)).astype(np.float32)
    # сглаживание (smoothstep), иначе видны ромбы
    xf = xf * xf * (3 - 2 * xf)
    yf = yf * yf * (3 - 2 * yf)
    a = grid[yi, xi]
    b = grid[yi, xi + 1]
    c = grid[yi + 1, xi]
    d = grid[yi + 1, xi + 1]
    top = a + (b - a) * xf
    bot = c + (d - c) * xf
    return top + (bot - top) * yf


def field_angle(x, y, t, grids):
    """fBm -> угол движения. Медленная эволюция во времени = дыхание поля."""
    total = np.zeros_like(x, dtype=np.float32)
    amp, freq, norm = 1.0, 1.0, 0.0
    for i, g in enumerate(grids):
        total += amp * value_noise(x * freq + i * 17.3, y * freq + t * (0.35 + 0.1 * i), g)
        norm += amp
        amp *= 0.5
        freq *= 2.1
    total /= norm
    return total * (2 * math.pi) * 1.7 + t * 0.12


# ---------------------------------------------------------------- палитра

def palette_lut(colors, steps=512):
    arr = np.array(colors, dtype=np.float32) / 255.0
    xs = np.linspace(0, 1, len(arr))
    t = np.linspace(0, 1, steps)
    lut = np.stack([np.interp(t, xs, arr[:, c]) for c in range(3)], axis=1)
    return lut.astype(np.float32)


# ---------------------------------------------------------------- рендер

def render(seed, palette, seconds, fps, width, height, particles,
           on_frame=None, warmup=None):
    rng = np.random.default_rng(seed)
    bg, colors = PALETTES[palette]
    lut = palette_lut(colors)

    grids = [_grid(s, rng) for s in (4, 8, 16, 32)]

    # стартовые позиции: мягкое кольцо вокруг центра, чтобы был «поток»
    ang = rng.random(particles) * 2 * math.pi
    rad = 0.08 + rng.random(particles) ** 0.6 * 0.42
    px = 0.5 + np.cos(ang) * rad * (width / height)
    py = 0.5 + np.sin(ang) * rad
    speed = 0.0016 + rng.random(particles) ** 2 * 0.0034
    age = rng.random(particles).astype(np.float32)
    hue = rng.random(particles).astype(np.float32)

    canvas = np.zeros((height, width, 3), dtype=np.float32)
    canvas += np.array(bg, dtype=np.float32) / 255.0 * 0.35

    total_frames = int(seconds * fps)
    warm = warmup if warmup is not None else int(total_frames * 0.25)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    vign = 1.0 - 0.55 * (((xx / width - 0.5) ** 2 + (yy / height - 0.5) ** 2) * 2.4) ** 1.3
    vig = np.clip(vign, 0.25, 1.0)[..., None]

    frames = []
    for f in range(total_frames + warm):
        t = f / max(1, fps)
        prev_px, prev_py = px.copy(), py.copy()

        a = field_angle(px, py, t * 0.55, grids)
        # лёгкое притяжение к центру — трассы не разлетаются в бесконечность
        cx, cy = 0.5 - px, 0.5 - py
        d = np.sqrt(cx * cx + cy * cy) + 1e-6
        px = px + np.cos(a) * speed + cx / d * 0.00022
        py = py + np.sin(a) * speed + cy / d * 0.00022

        # «перерождение» частиц, чтобы поле не выгорало
        age = (age + 0.0022) % 1.0
        respawn = (np.abs(px - 0.5) > 0.62) | (np.abs(py - 0.5) > 0.62)
        if respawn.any():
            n = int(respawn.sum())
            ang_r = rng.random(n) * 2 * math.pi
            rad_r = rng.random(n) ** 0.7 * 0.1
            px[respawn] = 0.5 + np.cos(ang_r) * rad_r * (width / height)
            py[respawn] = 0.5 + np.sin(ang_r) * rad_r
            age[respawn] = 0.0

        # следы: 3 интерполяционные точки на сегмент
        for k in (0.0, 0.34, 0.67, 1.0):
            xi = ((prev_px + (px - prev_px) * k) * (width - 1)).astype(np.int32)
            yi = ((prev_py + (py - prev_py) * k) * (height - 1)).astype(np.int32)
            np.clip(xi, 0, width - 1, out=xi)
            np.clip(yi, 0, height - 1, out=yi)
            li = ((hue * 0.7 + age * 0.3 * (1 - k) * 3).clip(0, 0.999) * (len(lut) - 1)).astype(np.int32)
            col = lut[li]
            np.add.at(canvas, (yi, xi), col * 0.30)

        canvas *= 0.972  # затухание = длина хвоста

        if f < warm:
            continue

        # тональная компрессия + bloom
        x = canvas.copy()
        x = x / (1.0 + x * 0.85)
        x = np.power(np.clip(x, 0, 1), 0.92)
        x = x * vig

        img = Image.fromarray((np.clip(x, 0, 1) * 255).astype(np.uint8))
        bright = img.filter(ImageFilter.GaussianBlur(9))
        img = Image.blend(img, Image.blend(img, bright, 0.55), 0.45)
        img = img.filter(ImageFilter.GaussianBlur(0.4))

        arr = np.asarray(img).astype(np.int16)
        grain = (rng.standard_normal((height, width, 1)) * 3.5).astype(np.int16)
        arr = np.clip(arr + grain, 0, 255).astype(np.uint8)

        # fade in / fade out по краям фильма
        ft = (f - warm) / max(1, total_frames - 1)
        fade = min(1.0, ft / 0.12) * min(1.0, (1.0 - ft) / 0.12)
        if fade < 1.0:
            arr = (arr.astype(np.float32) * max(0.0, fade)).astype(np.uint8)

        frames.append(arr)
        if on_frame:
            on_frame(len(frames), total_frames)

    return frames


# ---------------------------------------------------------------- звук

def soundtrack(seed, seconds, sample_rate=44100, path="out/score.wav"):
    """Аддитивный синтез: дрон, квинта, шумовой «ветер», редкие колокольчики."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sample_rate)
    t = np.arange(n) / sample_rate

    root = 55.0 * (2 ** (rng.integers(0, 3) / 12))   # A1 и соседи
    sig = np.zeros(n, dtype=np.float64)
    for mult, amp, detune in ((1.0, 0.34, 0.0), (1.005, 0.22, 0.0),
                              (1.5, 0.16, 0.15), (2.0, 0.10, -0.2), (3.0, 0.05, 0.3)):
        lfo = 0.6 + 0.4 * np.sin(2 * math.pi * (0.05 + detune) * t + detune * 6)
        sig += amp * lfo * np.sin(2 * math.pi * root * mult * t)

    # шумовой «ветер» через простой однополюсный фильтр
    noise = rng.standard_normal(n)
    lp = np.zeros(n)
    a = 0.02
    acc = 0.0
    for i in range(0, n, 1):
        acc += a * (noise[i] - acc)
        lp[i] = acc
    sig += 0.9 * lp / (np.max(np.abs(lp)) + 1e-9) * (0.35 + 0.25 * np.sin(2 * math.pi * 0.07 * t))

    # колокольчики: редкие затухающие синусы на пентатонике
    scale = [0, 3, 5, 7, 10, 12]
    pos = 0.8
    while pos < seconds - 0.6:
        note = root * 4 * 2 ** (rng.choice(scale) / 12)
        start = int(pos * sample_rate)
        ln = int(1.6 * sample_rate)
        env = np.exp(-np.linspace(0, 5.5, ln))
        seg = 0.16 * env * np.sin(2 * math.pi * note * np.arange(ln) / sample_rate)
        end = min(n, start + ln)
        sig[start:end] += seg[: end - start]
        pos += float(rng.uniform(0.9, 2.4))

    # мастер: мягкий фейд, стерео с расхождением, нормализация
    fade = int(0.4 * sample_rate)
    sig[:fade] *= np.linspace(0, 1, fade)
    sig[-fade:] *= np.linspace(1, 0, fade)
    sig = sig / (np.max(np.abs(sig)) + 1e-9) * 0.82
    left = sig
    right = np.roll(sig, int(0.011 * sample_rate)) * 0.97

    stereo = np.stack([left, right], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path


# ---------------------------------------------------------------- ffmpeg

def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg не найден: apt install ffmpeg или pip install imageio-ffmpeg")


def encode(frames, width, height, fps, out_path, audio=None):
    ffmpeg = find_ffmpeg()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-"]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += [out_path]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in frames:
        proc.stdin.write(fr.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("ffmpeg упал на кодировании")
    return out_path


def save_poster(seed, palette, width, height, particles, out_path, settle=90):
    """Один кадр в высоком разрешении — «отпечаток» фильма."""
    frames = render(seed, palette, seconds=settle / 24, fps=24, width=width,
                    height=height, particles=particles)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    Image.fromarray(frames[-1]).save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--palette", default="ember", choices=sorted(PALETTES))
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--particles", type=int, default=2200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--poster", default=None)
    ap.add_argument("--audio", action="store_true", help="синтезировать саундтрек")
    args = ap.parse_args()

    print(f"seed={args.seed} palette={args.palette} {args.width}x{args.height} "
          f"{args.seconds}s@{args.fps}fps", flush=True)

    frames = render(args.seed, args.palette, args.seconds, args.fps,
                    args.width, args.height, args.particles,
                    on_frame=lambda i, n: print(f"  frame {i}/{n}", end="\r", flush=True))
    print()

    audio = None
    if args.audio:
        audio = soundtrack(args.seed, args.seconds, path=os.path.join(
            os.path.dirname(args.out) or ".", "score.wav"))

    encode(frames, args.width, args.height, args.fps, args.out, audio)
    print("video:", args.out)

    if args.poster:
        save_poster(args.seed, args.palette, int(args.height * 1.4), int(args.height * 1.9),
                    args.particles * 2, args.poster)
        print("poster:", args.poster)


if __name__ == "__main__":
    main()
