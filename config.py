from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

_here = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Profile:
    name: str
    scale: str
    bitrate: str
    bandwidth: int
    res: str
    threshold: int


@dataclass
class AudioConfig:
    codec: str = "aac"
    bitrate: str = "128k"


@dataclass
class HlsConfig:
    segment_duration: int = 4
    segment_type: str = "fmp4"
    playlist_type: str = "vod"
    keyframe_interval: int = 60


@dataclass
class Config:
    # Input / identity
    input_video: str = "video_output.mp4"
    video_id: str = "video-xyz"

    # Paths
    output_dir: str = "my_processed_video"
    mc_alias_path: str = "local_s3/video-streams2"

    # Codec
    video_codec: str = "libx265"
    video_codec_tag: Optional[str] = "hvc1"
    x265_params: Optional[str] = "keyint=60:min-keyint=60:scenecut=0"
    preset: str = "medium"
    crf: Optional[int] = None
    pixel_format: str = "yuv420p"

    # Audio
    audio: AudioConfig = field(default_factory=AudioConfig)

    # HLS
    hls: HlsConfig = field(default_factory=HlsConfig)

    # Quality ladder (profiles active for this run – filtered by source height)
    profiles: List[Profile] = field(default_factory=lambda: [
        Profile("1080p", "1920:-2", "2500k", 2500000, "1920x1080", 1080),
        Profile("720p",  "1280:-2", "1250k", 1250000, "1280x720",  720),
        Profile("480p",  "854:-2",   "500k",  500000,  "854x480",   480),
    ])

    # Fallback profile used when source height is below the lowest threshold
    fallback_profile: Profile = field(default_factory=lambda: Profile(
        "source", "trunc(iw/2)*2:trunc(ih/2)*2",
        "600k", 600000, "native", 0,
    ))

    # Behaviour flags
    clean_local: bool = True
    clean_remote: bool = True
    upload: bool = True


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
