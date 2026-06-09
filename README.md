# HVEC-encoding

HLS video packaging pipeline that transcodes source videos into adaptive-bitrate HLS streams and uploads them to S3-compatible storage.

## Requirements

- **ffmpeg** — video/audio transcoding and HLS packaging
- **ffprobe** — source video metadata analysis
- **mc** (MinIO Client) — upload to S3-compatible storage

## Overview

The pipeline inspects the input video with `ffprobe`, builds an adaptive resolution/bitrate ladder (e.g. 1080p, 720p, 480p), transcodees each variant into HLS segments (fMP4 or TS) via `ffmpeg`, generates a `master.m3u8` manifest, and uploads everything to a configurable S3 bucket path using `mc`.

## Status

This repository contains experimental scripts exploring different encoding strategies:

| Script | Codec | Bitrate Strategy | Segment Format |
|--------|-------|-----------------|----------------|
| `process_video.py` | H.264 | Fixed CBR | TS |
| `process_video2.py` | H.264 | Source-capped CBR | TS |
| `hevc.py` | H.265 | Fixed CBR | fMP4 |
| `hevc2.py` | H.265 | Source-adaptive CBR | fMP4 |
| `hevc2_2.py` | H.265 | Source-adaptive CBR (S3 purge) | fMP4 |
| `qwen-h265.py` | H.265 | CRF + capped maxrate | fMP4 |
| `qwen2.py` | H.265 | CRF + dynamic maxrate | fMP4 |
| `av1.py` | AV1 (SVT) | Fixed CBR | fMP4 |
| `av1-ds.py` | AV1 (SVT) | Pixel-ratio source-adaptive | fMP4 |

All scripts share the same basic pipeline structure. Future work will consolidate these into a single configurable tool with an automated testing/benchmarking framework to compare codec/preset/bitrate trade-offs.
