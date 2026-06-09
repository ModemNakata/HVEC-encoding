from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from config import Config, Profile
from pipeline.probe import VideoMeta


def run(config: Config, profile: Profile, meta: VideoMeta) -> None:
    print(f"[transcode] encoding {profile.name} ({profile.res}) @ {profile.bitrate}")

    playlist = os.path.join(config.output_dir, f"{profile.name}.m3u8")
    seg_pattern = os.path.join(config.output_dir, f"{profile.name}_%03d.m4s")

    cmd = ["ffmpeg", "-y", "-i", config.input_video]

    # Scaling
    cmd += ["-vf", f"scale={profile.scale}"]

    # Video encoder
    cmd += ["-c:v", config.video_codec]
    cmd += ["-b:v", profile.bitrate]

    # CRF (if set, -b:v becomes a fallback)
    if config.crf is not None:
        cmd += ["-crf", str(config.crf)]

    cmd += ["-pix_fmt", config.pixel_format]

    if config.video_codec_tag:
        cmd += ["-vtag", config.video_codec_tag]

    # libvpx / VP9 specifics
    if config.video_codec == "libvpx-vp9":
        cmd += ["-deadline", config.deadline]
        cmd += ["-cpu-used", str(config.cpu_used)]
    else:
        cmd += ["-preset", config.preset]

    if config.codec_params:
        cmd += config.codec_params

    # Keyframes
    cmd += ["-g", str(config.hls.keyframe_interval)]
    cmd += ["-sc_threshold", "0"]

    # Audio
    cmd += ["-c:a", config.audio.codec, "-b:a", config.audio.bitrate]

    # HLS packaging
    cmd += ["-hls_time", str(config.hls.segment_duration)]
    cmd += ["-hls_playlist_type", config.hls.playlist_type]
    cmd += ["-hls_segment_type", config.hls.segment_type]
    cmd += ["-hls_fmp4_init_filename", f"{profile.name}_init.mp4"]
    cmd += ["-hls_segment_filename", seg_pattern]
    cmd += ["-hls_flags", "independent_segments"]
    cmd += ["-start_number", "0"]

    cmd.append(playlist)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[transcode] ERROR: {profile.name} failed:\n{proc.stderr[-500:]}")
        sys.exit(1)

    segs = list(Path(config.output_dir).glob(f"{profile.name}_*.m4s"))
    total_mb = sum(f.stat().st_size for f in segs) / (1024 * 1024)
    print(f"[transcode] {profile.name}: {len(segs)} segments, {total_mb:.1f} MB total")
