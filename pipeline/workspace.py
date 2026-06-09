from __future__ import annotations

import os
import shutil
import subprocess
import sys

from config import Config


def clean_local(config: Config) -> None:
    if not config.clean_local:
        return
    if os.path.exists(config.output_dir):
        print(f"[workspace] removing stale local directory: {config.output_dir}")
        shutil.rmtree(config.output_dir)
    os.makedirs(config.output_dir, exist_ok=True)
    print(f"[workspace] created fresh staging directory: {config.output_dir}")


def clean_remote(config: Config) -> None:
    if not config.clean_remote:
        return
    dest = f"{config.mc_alias_path}/{config.video_id}/"
    print(f"[workspace] purging remote path: {dest}")
    subprocess.run(
        ["mc", "rm", "--recursive", "--force", dest],
        capture_output=True,
    )
    print(f"[workspace] remote workspace purged")
