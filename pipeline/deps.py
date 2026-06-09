from __future__ import annotations

import os
import shutil
import subprocess
import sys

from config import Config


def check(config: Config) -> None:
    for tool in ("ffmpeg", "ffprobe", "mc"):
        if not shutil.which(tool):
            print(f"[deps] ERROR: required tool '{tool}' not found in PATH")
            sys.exit(1)

    if not os.path.exists(config.input_video):
        print(f"[deps] ERROR: input file '{config.input_video}' not found")
        sys.exit(1)

    # Verify encoder is available in ffmpeg
    enc = config.video_codec
    r = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True)
    if enc not in r.stdout:
        print(f"[deps] ERROR: encoder '{enc}' not available in ffmpeg")
        sys.exit(1)

    print(f"[deps] all dependencies satisfied")
