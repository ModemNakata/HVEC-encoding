from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from config import Config, Profile, calc_maxrate, calc_bufsize, build_scale
from pipeline.probe import VideoMeta


def run(config: Config, profile: Profile, meta: VideoMeta) -> str:
    """Transcode one profile variant. Returns the actual output resolution string."""
    source_kbps = meta.bitrate_bps // 1000
    maxrate = calc_maxrate(profile.ceiling_kbps, source_kbps, config.cap_scale)
    bufsize = calc_bufsize(maxrate, config.buf_factor)

    scale_filter, actual_res = build_scale(profile, meta.width, meta.height)

    print(f"[transcode] {profile.name} ({actual_res})  "
          f"crf={config.crf}  maxrate={maxrate}k  bufsize={bufsize}k")

    playlist = os.path.join(config.output_dir, f"{profile.name}.m3u8")
    seg_pattern = os.path.join(config.output_dir, f"{profile.name}_%03d.m4s")

    cmd = ["ffmpeg", "-y", "-i", config.input_video]

    cmd += ["-vf", scale_filter]

    cmd += ["-c:v", config.video_codec]
    cmd += ["-crf", str(config.crf)]
    cmd += ["-maxrate", f"{maxrate}k"]
    cmd += ["-bufsize", f"{bufsize}k"]

    cmd += ["-preset", config.preset]
    cmd += ["-pix_fmt", config.pixel_format]

    if config.video_codec_tag:
        cmd += ["-vtag", config.video_codec_tag]

    if config.codec_params:
        if config.video_codec == "libx265":
            cmd += ["-x265-params", config.codec_params]
        elif config.video_codec == "libx264":
            cmd += ["-x264-params", config.codec_params]

    cmd += ["-g", str(config.hls.keyframe_interval)]
    cmd += ["-sc_threshold", "0"]

    ab_kbps = max(64, meta.audio_bitrate_bps // 1000) if meta.audio_bitrate_bps else 128
    cmd += ["-c:a", "aac", "-b:a", f"{ab_kbps}k"]

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

    return actual_res
