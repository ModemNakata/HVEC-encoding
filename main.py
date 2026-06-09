#!/usr/bin/env python3
from config import Config, filter_profiles, build_fallback
from pipeline import deps, probe, workspace, transcode, manifest, upload


def main() -> None:
    cfg = Config(
        input_video="video_output.mp4",
        video_id="video-xyz",
        output_dir="my_processed_video",
        mc_alias_path="local_s3/video-streams",
        video_codec="libx265",
        video_codec_tag="hvc1",
        x265_params="keyint=60:min-keyint=60:scenecut=0",
        preset="medium",
        # crf=23,
        clean_local=True,
        clean_remote=True,
        upload=True,
    )

    print("=== HLS PACKAGING PIPELINE ===")

    deps.check(cfg)

    meta = probe.probe(cfg)

    workspace.clean_local(cfg)
    if cfg.clean_remote:
        workspace.clean_remote(cfg)

    profiles = filter_profiles(cfg.profiles, meta.height)
    if not profiles:
        profiles = [build_fallback(meta.height, cfg.fallback_profile)]

    print(f"[main] active profiles: {[p.name for p in profiles]}")

    for p in profiles:
        transcode.run(cfg, p, meta)

    manifest.generate(cfg, profiles)
    upload.run(cfg)

    print("=== PIPELINE COMPLETE ===")
    print(f"Stream: {cfg.mc_alias_path}/{cfg.video_id}/master.m3u8")


if __name__ == "__main__":
    main()
