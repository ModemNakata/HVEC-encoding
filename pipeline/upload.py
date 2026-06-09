from __future__ import annotations

import subprocess
import sys

from config import Config


def run(config: Config) -> None:
    if not config.upload:
        return

    dest = f"{config.mc_alias_path}/{config.video_id}/"
    print(f"[upload] uploading to {dest}")

    proc = subprocess.run(
        ["mc", "cp", "--recursive", f"{config.output_dir}/", dest],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"[upload] ERROR:\n{proc.stderr}")
        sys.exit(1)
    print(f"[upload] done")
