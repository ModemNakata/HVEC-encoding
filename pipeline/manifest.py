from __future__ import annotations

import os

from config import Config, Profile


def generate(config: Config, profiles: list[Profile],
             actual_resolutions: dict[str, str]) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", ""]
    for p in profiles:
        res = actual_resolutions.get(p.name, p.res if hasattr(p, 'res') else f"{p.ref_width}x?")
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={p.bandwidth},RESOLUTION={res}\n"
            f"{p.name}.m3u8\n"
        )
    content = "\n".join(lines)

    path = os.path.join(config.output_dir, "master.m3u8")
    with open(path, "w") as f:
        f.write(content)
    print(f"[manifest] written: {path}")
    return path
