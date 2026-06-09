from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass

from config import Config


@dataclass
class VideoMeta:
    width: int
    height: int
    bitrate_bps: int
    codec: str
    fps: float
    duration_s: float


def probe(config: Config) -> VideoMeta:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,bit_rate,codec_name,r_frame_rate",
        "-show_entries", "format=bit_rate,duration",
        "-of", "json",
        config.input_video,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[probe] ffprobe failed:\n{proc.stderr}")
        sys.exit(1)

    try:
        data = json.loads(proc.stdout)
        s = data["streams"][0]
        fmt = data["format"]

        w = int(s["width"])
        h = int(s["height"])
        codec = s.get("codec_name", "unknown")

        num, den = str(s.get("r_frame_rate", "30/1")).split("/")
        fps = int(num) / int(den)

        br = s.get("bit_rate") or fmt.get("bit_rate", "0")
        br = int(br) if br not in ("N/A", "0") else 0

        dur = float(fmt.get("duration", 0))

    except (KeyError, IndexError, ValueError) as e:
        print(f"[probe] failed to parse ffprobe output: {e}")
        sys.exit(1)

    meta = VideoMeta(width=w, height=h, bitrate_bps=br, codec=codec, fps=fps, duration_s=dur)
    print(f"[probe] {meta.width}x{meta.height} ({meta.height}p)  {meta.codec}"
          f"  {meta.bitrate_bps // 1000} kbps  {meta.fps:.2f} fps"
          f"  {meta.duration_s:.1f}s")
    return meta
