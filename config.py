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

    # ── Video codec ───────────────────────────────────────────────────────────
    video_codec: str = "libx265"
    video_codec_tag: Optional[str] = "hvc1"

    # Encoder params (e.g. "keyint=60:min-keyint=60:scenecut=0" for libx265)
    codec_params: Optional[str] = None

    # x264/x265: ultrafast … placebo
    preset: str = "medium"

    # ── Capped CRF rate control ───────────────────────────────────────────────

    # CRF: 0–51 (lower = better). ~23 = good, ~28 = default.
    crf: int = 23
    # maxrate = min(profile.ceiling_kbps, source_kbps * cap_scale)
    cap_scale: float = 0.8
    # bufsize = int(maxrate * buf_factor)
    buf_factor: float = 1.5

    pixel_format: str = "yuv420p"

    # ── HLS ───────────────────────────────────────────────────────────────────
    hls: HlsConfig = field(default_factory=HlsConfig)

    # ── Quality ladder ────────────────────────────────────────────────────────
    profiles: List[Profile] = field(default_factory=lambda: [
        Profile("1440p", 6000000, 2560, 1440, 6000),
        Profile("1080p", 3000000, 1920, 1080, 3000),
        Profile("720p",  1500000, 1280,  720, 1500),
    ])

    fallback_profile: Profile = field(default_factory=lambda: Profile(
        "source", 600000, 1920, 0, 600,
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
    """Return (scale_filter, actual_resolution) for the given source."""
    if src_w >= src_h:
        w = profile.ref_width
        h = int(w * src_h / src_w / 2) * 2
        return f"scale={w}:-2", f"{w}x{h}"
    else:
        h = profile.ref_width
        w = int(h * src_w / src_h / 2) * 2
        return f"scale=-2:{h}", f"{w}x{h}"
