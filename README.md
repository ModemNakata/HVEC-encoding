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

### Codec notes

**H.264** is the default — it has the widest browser/device support of any
codec and works everywhere without additional plugins.

If you need better compression, switch to **HEVC (H.265)** in `config.py`
(`video_codec = "libx265"`, `video_codec_tag = "hvc1"`). HEVC support is
narrower on desktop — only Safari (macOS) and Edge (Windows) support it
natively in browsers; Chrome and Firefox do not.
