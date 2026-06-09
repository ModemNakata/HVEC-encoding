from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Profile:
    """A single rung in the adaptive bitrate ladder."""
    name: str          # label, e.g. "1080p"
    bandwidth: int     # HLS BANDWIDTH in bps
    ref_width: int     # long-edge target px (e.g. 1920 for "1080p")
    threshold: int     # min short-edge to include this rung (e.g. 1080)
    ceiling_kbps: int  # hard maxrate cap (e.g. 3000)


@dataclass
class HlsConfig:
    segment_duration: int = 4
    segment_type: str = "fmp4"
    playlist_type: str = "vod"
    keyframe_interval: int = 60


@dataclass
class Config:

    # ── Input / identity ──────────────────────────────────────────────────────
    input_video: str = "video_output.mp4"
    video_id: str = "video-xyz"

    # ── Paths ─────────────────────────────────────────────────────────────────
    output_dir: str = "my_processed_video"
    mc_alias_path: str = "local_s3/video-streams"

    # ── Video codec: H.264 ────────────────────────────────────────────────────
    # libx264 is the software H.264 encoder built into ffmpeg.
    # H.264 has the widest device and browser support of any codec.
    video_codec: str = "libx264"

    # avc1 tag is the standard H.264 identifier for Apple / browser playback.
    video_codec_tag: Optional[str] = "avc1"

    # libx264 internal parameters flushed as a colon-separated string.
    # - keyint=60 / min-keyint=60 ensures a keyframe every ~2s (at 30fps)
    # - scenecut=0 prevents extra keyframes from scene changes (keeps segments uniform)
    codec_params: Optional[str] = "keyint=60:min-keyint=60:scenecut=0"

    # Speed-vs-compression trade-off. Slower = better compression = smaller file.
    # ultrafast > superfast > veryfast > faster > fast >
    # medium > slow > slower > veryslow > placebo
    preset: str = "slow"

    # ── Capped CRF rate control ───────────────────────────────────────────────

    # CRF (Constant Rate Factor): primary quality control, 0–51 (lower = better).
    #   18 = visually lossless
    #   23 = good quality (typical default for H.264)
    #   28 = smaller file, visible quality loss
    crf: int = 23

    # maxrate caps the peak bitrate so the output never inflates above the source.
    #   maxrate = min(profile.ceiling_kbps, source_bitrate_kbps * cap_scale)
    #   cap_scale=0.9 means we allow up to 90% of the source's bitrate.
    #   If the source is 2000kbps, maxrate caps at 1800k (10% reduction minimum).
    cap_scale: float = 0.9

    # bufsize = maxrate * buf_factor — the VBV buffer window for the rate controller.
    # A factor of 2× gives the encoder enough room to handle complex scenes smoothly.
    buf_factor: float = 2

    # 8-bit 4:2:0 — safest pixel format for browser/device compatibility.
    pixel_format: str = "yuv420p"

    # ── HLS ───────────────────────────────────────────────────────────────────
    hls: HlsConfig = field(default_factory=HlsConfig)

    # ── Quality ladder ────────────────────────────────────────────────────────
    # H.264 ceilings are roughly 2× HEVC at the same resolution.
    # The source*cap_scale cap still prevents inflation on low-bitrate uploads.
    profiles: List[Profile] = field(default_factory=lambda: [
        Profile("1440p", 12000000, 2560, 1440, 12000),
        Profile("1080p",  6000000, 1920, 1080,  6000),
        Profile("720p",   3000000, 1280,  720,  3000),
        Profile("480p",   1200000,  854,  480,  1200),
    ])

    fallback_profile: Profile = field(default_factory=lambda: Profile(
        "source", 1600000, 1920, 0, 1600,
    ))

    # ── Behaviour flags ───────────────────────────────────────────────────────
    clean_local: bool = True
    clean_remote: bool = True
    upload: bool = True


# ── helpers ──────────────────────────────────────────────────────────────────

def filter_profiles(profiles: List[Profile], source_min_dim: int) -> List[Profile]:
    return [p for p in profiles if source_min_dim >= p.threshold]


def build_fallback(source_min_dim: int, fallback: Profile) -> Profile:
    bw = max(200000, fallback.bandwidth)
    return Profile(
        name=fallback.name,
        bandwidth=bw,
        ref_width=fallback.ref_width,
        threshold=0,
        ceiling_kbps=fallback.ceiling_kbps,
    )


def calc_maxrate(ceiling_kbps: int, source_kbps: int, cap_scale: float) -> int:
    return min(ceiling_kbps, int(source_kbps * cap_scale))


def calc_bufsize(maxrate_kbps: int, buf_factor: float) -> int:
    return int(maxrate_kbps * buf_factor)


def build_scale(profile: Profile, src_w: int, src_h: int) -> Tuple[str, str]:
    """Return (ffmpeg scale filter, actual output resolution) for the given source."""
    if src_w >= src_h:
        # Landscape: fix width to ref_width, compute height proportionally
        w = profile.ref_width
        h = int(w * src_h / src_w / 2) * 2
        return f"scale={w}:-2", f"{w}x{h}"
    else:
        # Portrait: fix height to ref_width, compute width proportionally
        h = profile.ref_width
        w = int(h * src_w / src_h / 2) * 2
        return f"scale=-2:{h}", f"{w}x{h}"
