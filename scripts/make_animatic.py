#!/usr/bin/env python3
"""
Собирает «кино-аниматик» из набора кадров: наезды/отъезды камеры,
кроссфейды, зерно, виньетка, титры. Нужен только ffmpeg (в CI/Sandbox —
через `pip install imageio-ffmpeg`, локально — системный).

Использование:
    python3 scripts/make_animatic.py media-shots -o output/animatic.mp4 \
        --title "VOID WALKER" --subtitle "an AI film study" --width 2560 --height 1440

Раскладка движения по кадрам задаётся в SHOT_PLAN по порядку файлов:
    ("in",  amount)  — наезд (zoom 1.00 -> 1.00+amount)
    ("out", amount)  — отъезд (zoom 1.00+amount -> 1.00)
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

FPS = 24
TRANSITION = 0.6  # длительность кроссфейда, сек


def find_ffmpeg() -> str:
    for candidate in ("ffmpeg",):
        path = shutil.which(candidate)
        if path:
            return path
    try:
        import imageio_ffmpeg  # noqa: WPS433

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover
        sys.exit("ffmpeg не найден: apt install ffmpeg  или  pip install imageio-ffmpeg")


def build_scene(ffmpeg, image, out_path, width, height, duration, direction, amount):
    frames = max(2, int(round(duration * FPS)))
    # Увеличенный холст, чтобы zoompan не «дрожал» на субпикселях
    big_w, big_h = width * 2, height * 2
    if direction == "out":
        zexpr = f"1.{int(round(amount * 100)):02d}-{amount}*on/{frames}"
    else:
        zexpr = f"1.0+{amount}*on/{frames}"
    vf = (
        f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
        f"crop={big_w}:{big_h},"
        f"zoompan=z='{zexpr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={width}x{height}:fps={FPS},"
        f"setsar=1,format=yuv420p"
    )
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1", "-t", f"{duration:.3f}", "-i", image,
        "-vf", vf, "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def concat_with_xfade(ffmpeg, clips, durations, out_path, workdir, width, height):
    if len(clips) == 1:
        shutil.copy(clips[0], out_path)
        return out_path

    inputs = []
    for clip in clips:
        inputs += ["-i", clip]

    filters = []
    prev = "[0:v]"
    offset = 0.0
    for i in range(1, len(clips)):
        offset += durations[i - 1] - TRANSITION
        label = f"[x{i}]" if i < len(clips) - 1 else "[vout]"
        filters.append(
            f"{prev}[{i}:v]xfade=transition=fade:duration={TRANSITION}:offset={offset:.3f}{label}"
        )
        prev = f"[x{i}]"
    graph = ";".join(filters)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", graph, "-map", "[vout]", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", out_path]
    subprocess.run(cmd, check=True)
    return out_path


def make_title_card(width, height, title, subtitle, out_png):
    """Прозрачный PNG с титрами: рисуем PIL'ом, чтобы не зависеть от drawtext/libfreetype."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: WPS433

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def load(size, bold):
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def center(text, font, y, fill, shadow_alpha=140):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        x = (width - (right - left)) / 2 - left
        draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), text, font=font, fill=fill)
        return bottom - top

    if title:
        spaced = "  ".join(title.upper())
        th = center(spaced, load(int(height * 0.062), True), int(height * 0.70), (255, 255, 255, 240))
    else:
        th = 0
    if subtitle:
        center(subtitle, load(int(height * 0.026), False),
               int(height * 0.70) + th + int(height * 0.035), (255, 255, 255, 205))

    img.save(out_png)
    return out_png


def make_title_frame(source_image, width, height, title, subtitle, out_png):
    """Готовит кадр-титр: затемнённый первый кадр + надписи (без drawtext/overlay)."""
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance  # noqa: WPS433

    img = Image.open(source_image).convert("RGB")
    img = img.resize((width, height), Image.LANCZOS)
    img = ImageEnhance.Brightness(img).enhance(0.45)
    img = ImageEnhance.Color(img).enhance(0.8)
    draw = ImageDraw.Draw(img)

    def load(size, bold):
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        for base in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def center(text, font, y, fill):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        x = (width - (right - left)) / 2 - left
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=fill)
        return bottom - top

    if title:
        th = center("  ".join(title.upper()), load(int(height * 0.075), True),
                    int(height * 0.44), (245, 242, 235))
    else:
        th = 0
    if subtitle:
        center(subtitle, load(int(height * 0.028), False),
               int(height * 0.44) + th + int(height * 0.045), (220, 214, 202))

    img.save(out_png)
    return out_png


def grade_and_titles(ffmpeg, src, out_path, total, width, height, title, subtitle, workdir,
                     grain=0, preset="medium"):
    fade_out_start = max(0.0, total - 0.9)
    base = "vignette=PI/5"
    if grain > 0:
        base = f"noise=alls={grain}:allf=t+u," + base

    vf = f"{base},fade=t=in:st=0:d=0.8,fade=t=out:st={fade_out_start:.2f}:d=0.9"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
           "-vf", vf, "-an", "-c:v", "libx264", "-preset", preset, "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("shots_dir")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--width", type=int, default=2560)
    parser.add_argument("--height", type=int, default=1440)
    parser.add_argument("--title", default="")
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--grain", type=int, default=0,
                        help="сила зерна 0-20 (дорого по CPU, 0 = выключено)")
    parser.add_argument("--preset", default="medium",
                        help="x264 preset: ultrafast..slow")
    args = parser.parse_args()

    ffmpeg = find_ffmpeg()
    shots = sorted(
        os.path.join(args.shots_dir, f)
        for f in os.listdir(args.shots_dir)
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    )
    if not shots:
        sys.exit(f"В {args.shots_dir} нет изображений")

    plan = [("in", 0.05, 3.0), ("in", 0.06, 5.0), ("in", 0.10, 4.0), ("in", 0.08, 3.0), ("out", 0.10, 4.5)]
    while len(plan) < len(shots):
        plan.append(("in", 0.06, 4.0))
    plan = plan[: len(shots)]

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        clips, durations = [], []
        if args.title or args.subtitle:
            title_png = make_title_frame(shots[0], args.width, args.height,
                                         args.title, args.subtitle,
                                         os.path.join(tmp, "title.png"))
            clip = os.path.join(tmp, "scene_title.mp4")
            print("[0] title card", flush=True)
            build_scene(ffmpeg, title_png, clip, args.width, args.height, 3.0, "in", 0.05)
            clips.append(clip)
            durations.append(3.0)

        for i, (shot, (direction, amount, duration)) in enumerate(zip(shots, plan[1:])):
            clip = os.path.join(tmp, f"scene{i}.mp4")
            print(f"[{i + 1}/{len(shots)}] {os.path.basename(shot)} {direction} {amount} {duration}s", flush=True)
            build_scene(ffmpeg, shot, clip, args.width, args.height, duration, direction, amount)
            clips.append(clip)
            durations.append(duration)

        stitched = os.path.join(tmp, "stitched.mp4")
        concat_with_xfade(ffmpeg, clips, durations, stitched, tmp, args.width, args.height)
        total = sum(durations) - TRANSITION * (len(clips) - 1)

        print(f"grade + titles -> {args.output} ({total:.1f}s)", flush=True)
        grade_and_titles(
            ffmpeg, stitched, args.output, total, args.width, args.height,
            args.title, args.subtitle, tmp,
            grain=args.grain, preset=args.preset,
        )

    size = os.path.getsize(args.output) / 1e6
    print(f"done: {args.output} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
