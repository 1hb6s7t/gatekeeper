"""Stitch the per-scene narration into one track and mux it onto the screencast.

Output: docs/report.mp4 (H.264 + AAC, playable everywhere)
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parent.parent
NARR = ROOT / "docs" / "narration"
RAW = ROOT / "docs" / "video_raw"
OUT = ROOT / "docs" / "report.mp4"


def concat_narration() -> Path:
    target = NARR / "narration.wav"
    parts = [NARR / f"s{i}.wav" for i in range(1, 9)]
    with wave.open(str(parts[0])) as first:
        params = first.getparams()
    with wave.open(str(target), "wb") as out:
        out.setparams(params)
        for part in parts:
            with wave.open(str(part)) as w:
                if w.getparams()[:3] != params[:3]:
                    raise SystemExit(f"{part.name} format differs from {parts[0].name}")
                out.writeframes(w.readframes(w.getnframes()))
    return target


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    narration = concat_narration()
    with wave.open(str(narration)) as w:
        audio_len = w.getnframes() / w.getframerate()

    videos = sorted(RAW.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        raise SystemExit("no recording in docs/video_raw — run scripts/record_report.py first")
    video = videos[-1]

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(narration),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(OUT),
    ]
    subprocess.run(cmd, check=True)

    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(OUT)], capture_output=True, text=True).stderr
    duration = next((ln.split("Duration: ")[1].split(",")[0] for ln in probe.splitlines() if "Duration:" in ln), "?")
    print(f"narration {audio_len:.1f}s  ->  {OUT.name} ({OUT.stat().st_size // 1024} KB, {duration})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
