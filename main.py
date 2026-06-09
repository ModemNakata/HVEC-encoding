#!/usr/bin/env python3
from config import Config, filter_profiles, build_fallback, build_scale
from pipeline import deps, probe, workspace, transcode, manifest, upload


def main() -> None:
    cfg = Config()

    print("=== HLS PACKAGING PIPELINE ===")

    deps.check(cfg)
    meta = probe.probe(cfg)

    workspace.clean_local(cfg)
    if cfg.clean_remote:
        workspace.clean_remote(cfg)

    # Use the short edge for profile matching (handles portrait/landscape)
    profiles = filter_profiles(cfg.profiles, meta.min_dim)
    if not profiles:
        profiles = [build_fallback(meta.min_dim, cfg.fallback_profile)]

    print(f"[main] active profiles: {[p.name for p in profiles]}")

    actual_resolutions: dict[str, str] = {}
    for p in profiles:
        actual_res = transcode.run(cfg, p, meta)
        actual_resolutions[p.name] = actual_res

    manifest.generate(cfg, profiles, actual_resolutions)
    upload.run(cfg)

    print("=== PIPELINE COMPLETE ===")
    print(f"Stream: {cfg.mc_alias_path}/{cfg.video_id}/master.m3u8")


if __name__ == "__main__":
    main()
