#!/usr/bin/env python3
"""
HLS packaging pipeline for HEVC (H.265).

Steps:
  1. Check system dependencies (ffmpeg, ffprobe, mc, libx265).
  2. Probe input video for resolution, bitrate, fps, duration, audio info.
  3. Clean local staging directory (wipe + recreate).
  4. Optionally purge the remote S3 bucket path for this video_id.
  5. Build the adaptive bitrate ladder — only profiles whose short-edge
     threshold (e.g. 720, 1080, 1440) fits the source are kept.
  6. Transcode each rung:
       - Scale to the target resolution (handles portrait vs landscape).
       - Apply capped-CRF rate control:
           crf      = quality target (lower = better)
           maxrate  = min(profile_ceiling, source_kbps * 0.9)
           bufsize  = maxrate * 2
       - Re-encode audio to AAC at the source audio bitrate (fallback 128k).
       - Package as HLS fMP4 segments with independent keyframes.
  7. Generate the master.m3u8 manifest with the actual output resolutions.
  8. Upload the entire staging directory to S3 via the mc client.
"""

from config import Config, filter_profiles, build_fallback
from pipeline import deps, probe, workspace, transcode, manifest, upload


def main() -> None:
    cfg = Config()

    print("=== HLS PACKAGING PIPELINE ===\n")

    # ── 1. Verify tooling ─────────────────────────────────────────────────
    deps.check(cfg)

    # ── 2. Read source metadata ───────────────────────────────────────────
    meta = probe.probe(cfg)

    # ── 3. Prepare local workspace ────────────────────────────────────────
    workspace.clean_local(cfg)

    # ── 4. Clear remote bucket ────────────────────────────────────────────
    if cfg.clean_remote:
        workspace.clean_remote(cfg)

    # ── 5. Build adaptive ladder ──────────────────────────────────────────
    # Compare against min(width,height) to handle portrait video correctly.
    # A 1080×1920 portrait video will not trigger the 1440p profile.
    profiles = filter_profiles(cfg.profiles, meta.min_dim)
    if not profiles:
        print(f"[main] source {meta.min_dim}p is below all profile thresholds, "
              "using fallback")
        profiles = [build_fallback(meta.min_dim, cfg.fallback_profile)]

    print(f"[main] active profiles: {[p.name for p in profiles]}\n")

    # ── 6. Transcode each variant ─────────────────────────────────────────
    actual_resolutions: dict[str, str] = {}
    for p in profiles:
        actual_res = transcode.run(cfg, p, meta)
        actual_resolutions[p.name] = actual_res
        print()

    # ── 7. Generate master manifest ───────────────────────────────────────
    manifest.generate(cfg, profiles, actual_resolutions)

    # ── 8. Upload to S3 ───────────────────────────────────────────────────
    upload.run(cfg)

    print("\n=== PIPELINE COMPLETE ===")
    print(f"Stream: {cfg.mc_alias_path}/{cfg.video_id}/master.m3u8")


if __name__ == "__main__":
    main()
