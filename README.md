# HVEC-encoding

HLS video packaging pipeline that transcodes source videos into adaptive-bitrate HLS streams using HEVC (H.265) and uploads them to S3-compatible storage.

## Requirements

- **ffmpeg** (with `libx265` support)
- **ffprobe**
- **mc** (MinIO Client)

## Pipeline

1. Probe source video metadata (resolution, bitrate, FPS, audio)
2. Build an adaptive ladder filtered by source height: 1440p / 1080p / 720p
3. Transcode each rung with capped-CRF rate control
4. Generate `master.m3u8` manifest
5. Upload to S3 via `mc`

Rate control guarantees output never inflates past the source:
`maxrate = min(profile_ceiling, source_bitrate * 0.9)`

Audio is re-encoded to AAC at the source bitrate (fallback 128k).

## Configuration

All settings in `config.py`:
- **`video_codec`** / **`video_codec_tag`** — codec and Apple compat tag
- **`crf`** — quality (0–51, lower = better, default 18)
- **`preset`** — speed/compression trade-off (default `slow`)
- **`cap_scale`** — maxrate ceiling as fraction of source bitrate
- **`buf_factor`** — VBV buffer multiplier
- **`profiles`** — resolution ladder with per-rung bitrate ceilings
- **`clean_local`** / **`clean_remote`** / **`upload`** — behaviour flags

## Compatibility note

HEVC (H.265) offers ~40–50% better compression than H.264 at equivalent
quality, but is slightly less compatible:

- **macOS / iOS** — fully supported (hardware decode on Apple Silicon +
  iPhone 6+; requires `hvc1` tag which is set by default).
- **Android** — supported from Android 5.0+, hardware on most devices.
- **Windows** — supported via built-in HEVC extensions or third-party
  players (VLC, mpv).
- **Linux** — software decode via ffmpeg; hardware varies by GPU/driver.
- **Smart TVs** — most 2016+ models support HEVC.

If you need **maximum compatibility** (pre-2016 devices, feature phones,
unusual browsers), switch to H.264 by changing `video_codec` and
`video_codec_tag` in `config.py`.
