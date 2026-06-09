from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Profile:
    """A single rung in the adaptive bitrate ladder."""
    name: str        # label used for filenames, e.g. "1080p"
    scale: str       # ffmpeg scale filter, e.g. "1920:-2" (keeps aspect ratio)
    bitrate: str     # target video bitrate, e.g. "1500k"
    bandwidth: int   # advertised HLS BANDWIDTH in bps, e.g. 1500000
    res: str         # advertised RESOLUTION, e.g. "1920x1080"
    threshold: int   # minimum source height to include this rung, e.g. 1080


@dataclass
class AudioConfig:
    codec: str = "aac"       # "aac" (best compat) / "libopus" (better quality)
    bitrate: str = "128k"


@dataclass
class HlsConfig:
    segment_duration: int = 4       # seconds per HLS chunk
    segment_type: str = "fmp4"      # "fmp4" (CMAF) / "mpegts"
    playlist_type: str = "vod"      # "vod" / "event" / None (live)
    keyframe_interval: int = 60     # GOP size in frames (60 @ 30fps = 2s)


@dataclass
class Config:

    # ── Input / identity ──────────────────────────────────────────────────────
    input_video: str = "video_output.mp4"
    video_id: str = "video-xyz"      # sub-folder in the S3 bucket

    # ── Paths ─────────────────────────────────────────────────────────────────
    output_dir: str = "my_processed_video"
    mc_alias_path: str = "local_s3/video-streams"   # mc alias + bucket prefix

    # ── Video codec ───────────────────────────────────────────────────────────
    # Common choices:
    #   "libvpx-vp9"    – best compression, slower encode, broad browser support
    #   "libx265"       – HEVC, good compression, hardware decode on modern devices
    #   "libx264"       – H.264, widest compatibility, least efficient
    #   "libsvtav1"     – AV1, best compression, very slow, needs special ffmpeg build
    video_codec: str = "libvpx-vp9"

    # Apple-compatible codec tag (e.g. "hvc1" for HEVC, "avc1" for H.264).
    # Not used by VP9/AV1.
    video_codec_tag: Optional[str] = None

    # Arbitrary extra ffmpeg flags appended verbatim to the encoder line.
    # Example: ["-svtav1-params", "tune=0:film-grain=8"]
    codec_params: Optional[List[str]] = None

    # ── libvpx-vp9 specific ───────────────────────────────────────────────────

    # deadline: quality/speed trade-off for libvpx
    #   "best"    – maximum compression, very slow (use for final encodes)
    #   "good"    – balanced (sweet spot for most use cases)
    #   "realtime"– fastest, lowest quality (streaming / live)
    deadline: str = "good"

    # cpu-used: 0..5, lower = slower / better compression
    #   0–2  – high quality (use with "best" deadline)
    #   3–4  – balanced (use with "good" deadline)
    #   5    – fastest (use with "realtime")
    cpu_used: int = 1

    # ── libx265 / libx264 / libsvtav1 specific ────────────────────────────────
    # Only used when video_codec is NOT libvpx-vp9.
    preset: str = "medium"
    #   x264/x265: "ultrafast" "superfast" "veryfast" "faster" "fast"
    #              "medium" "slow" "slower" "veryslow" "placebo"
    #   SVT-AV1:   0..13 (0=best quality, 8=default, 13=fastest)

    # ── Rate control ──────────────────────────────────────────────────────────

    # CRF (Constant Rate Factor) – quality target, not a hard bitrate cap.
    # When set, -b:v acts as a maximum rather than the target.
    #   VP9:  0–63  (0=lossless, ~15–25=visually lossless, ~31=default, 63=worst)
    #   x264: 0–51  (0=lossless, 18=visually lossless, 23=default, 51=worst)
    #   x265: 0–51  (same range, ~28 default)
    #   AV1:  0–63  (~25–35 typical range, ~35 default)
    crf: Optional[int] = None

    pixel_format: str = "yuv420p"   # "yuv420p" (8-bit) / "yuv420p10le" (10-bit)

    # ── Audio ─────────────────────────────────────────────────────────────────
    audio: AudioConfig = field(default_factory=AudioConfig)

    # ── HLS ───────────────────────────────────────────────────────────────────
    hls: HlsConfig = field(default_factory=HlsConfig)

    # ── Quality ladder ────────────────────────────────────────────────────────
    # Active profiles are filtered by source height so you never up-scale.
    #
    # VP9 bitrate targets (roughly 40-50% lower than H.265 at equivalent quality):
    profiles: List[Profile] = field(default_factory=lambda: [
        Profile("1080p", "1920:-2", "1500k", 1500000, "1920x1080", 1080),
        Profile("720p",  "1280:-2",  "750k",  750000, "1280x720",  720),
        Profile("480p",  "854:-2",   "350k",  350000,  "854x480",   480),
    ])

    # Fallback when source height is below the lowest profile threshold
    fallback_profile: Profile = field(default_factory=lambda: Profile(
        "source", "trunc(iw/2)*2:trunc(ih/2)*2",
        "400k", 400000, "native", 0,
    ))

    # ── Behaviour flags ───────────────────────────────────────────────────────
    clean_local: bool = True      # wipe + recreate output_dir before encoding
    clean_remote: bool = True     # mc rm --recursive target S3 path
    upload: bool = True           # mc cp --recursive local → S3 when done


# ── helpers ──────────────────────────────────────────────────────────────────

def filter_profiles(profiles: List[Profile], source_height: int) -> List[Profile]:
    return [p for p in profiles if source_height >= p.threshold]


def build_fallback(source_height: int, fallback: Profile) -> Profile:
    bw = max(200000, fallback.bandwidth)
    return Profile(
        name=fallback.name,
        scale=fallback.scale,
        bitrate=f"{bw // 1000}k",
        bandwidth=bw,
        res=f"{source_height}p",
        threshold=0,
    )
