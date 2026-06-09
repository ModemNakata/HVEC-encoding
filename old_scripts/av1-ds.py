#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
import logging
from pathlib import Path

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_VIDEO = "video_output.mp4"         # The raw source file
VIDEO_ID = "video-xyz"                   # Unique ID for the bucket folder
OUTPUT_DIR = "my_processed_video"        # Local staging folder
MC_ALIAS_PATH = "local_s3/video-streams" # Your bucket target path

# Configure verbose logging format
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("video_pipeline.log", mode="w")
    ]
)
logger = logging.getLogger("AV1-fMP4-Pipeline")

def check_dependencies():
    """Verify system prerequisites exist before running CPU intensive tasks."""
    logger.info("Step 1/6: Checking system dependencies...")
    
    for tool in ["ffmpeg", "ffprobe", "mc"]:
        path = shutil.which(tool)
        if path:
            logger.debug(f"Found required tool '{tool}' at: {path}")
        else:
            logger.error(f"Missing required tool: '{tool}'. Please ensure it is installed and in your PATH.")
            sys.exit(1)
            
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input file '{INPUT_VIDEO}' not found in the current directory.")
        sys.exit(1)
        
    ffmpeg_check = subprocess.run(["ffmpeg", "-encoders"], stdout=subprocess.PIPE, text=True, stderr=subprocess.DEVNULL)
    if "libsvtav1" not in ffmpeg_check.stdout:
        logger.error("Your system FFmpeg build lacks the 'libsvtav1' encoder plugin module.")
        sys.exit(1)

    logger.info("All dependencies and AV1 system modules successfully verified.")

def get_input_metadata():
    """Use ffprobe to pull video parameters (dimensions, bitrate, and audio codec)."""
    logger.info("Step 2/6: Inspecting input video properties via ffprobe...")
    
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,bit_rate,codec_name,r_frame_rate", 
        "-show_entries", "format=bit_rate,duration",
        "-of", "json", 
        INPUT_VIDEO
    ]
    
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        logger.error("Failed to parse video attributes with ffprobe.")
        sys.exit(1)
        
    try:
        metadata = json.loads(process.stdout)
        video_stream = metadata["streams"][0]
        width = video_stream["width"]
        height = video_stream["height"]
        
        # Get video codec
        codec = video_stream.get("codec_name", "unknown")
        
        # Get frame rate (handle fractional like 30000/1001)
        fps_str = video_stream.get("r_frame_rate", "30/1")
        num, den = fps_str.split("/")
        fps = round(int(num) / int(den))
        
        # Try to get video stream bitrate first, fall back to format bitrate
        bitrate_str = video_stream.get("bit_rate")
        if not bitrate_str or bitrate_str in ("N/A", "0"):
            bitrate_str = metadata.get("format", {}).get("bit_rate", "0")
        
        # Duration for progress estimation
        duration = float(metadata.get("format", {}).get("duration", 0))
        
        native_bitrate = int(bitrate_str) if bitrate_str and bitrate_str != "N/A" else 0
        
        logger.info(f"Source: {width}x{height} @ {fps}fps, {codec}, "
                     f"{native_bitrate/1000:.0f} Kbps, {duration:.1f}s")
        
        return width, height, native_bitrate, fps
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"Failed to read JSON payload returned by ffprobe: {e}")
        sys.exit(1)

def prepare_workspace():
    """Create a clean workspace environment, wiping out stale artifacts if necessary."""
    logger.info(f"Step 3/6: Preparing local directory staging workspace...")
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def calculate_target_bitrate(source_bitrate_kbps, source_height, target_height, preset_bitrate_kbps):
    """
    Calculate optimal target bitrate based on source quality.
    Never encode at a higher effective bitrate than the source.
    Uses pixel count ratio for more accurate scaling than height alone.
    """
    if not source_bitrate_kbps or source_bitrate_kbps == 0:
        logger.warning(f"Unknown source bitrate, using preset {preset_bitrate_kbps} Kbps for {target_height}p")
        return preset_bitrate_kbps
    
    # Scale by pixel count ratio (more accurate than height ratio)
    pixel_ratio = (target_height / source_height) ** 2
    
    # Maximum useful bitrate: 90% of source quality at this resolution
    max_useful = int(source_bitrate_kbps * pixel_ratio * 0.9)
    
    # Use the lower of our preset and what's actually useful
    optimal = min(preset_bitrate_kbps, max_useful)
    
    # Absolute minimum for watchable quality
    optimal = max(optimal, 200)
    
    logger.info(f"  {target_height}p bitrate: {optimal} Kbps "
                f"(source: {source_bitrate_kbps} Kbps, preset: {preset_bitrate_kbps}, "
                f"max useful: {max_useful})")
    
    return optimal

def run_ffmpeg_transcode(scale_filter, bitrate_kbps, short_name, source_fps):
    """Execute a single AV1 transcoding subprocess converting to fMP4 HLS."""
    logger.info(f"Step 4/6: Encoding AV1 {short_name} @ {bitrate_kbps} Kbps...")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.m4s")
    
    # Calculate keyframe interval (2 seconds)
    gop_size = source_fps * 2
    
    # Calculate buffer parameters for VBV (Video Buffering Verifier)
    bufsize = bitrate_kbps * 4  # 4 seconds of buffer
    maxrate = int(bitrate_kbps * 1.5)  # 1.5x peak
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        
        # Video filter
        "-vf", f"scale={scale_filter}",
        
        # AV1 Video encoding
        "-c:v", "libsvtav1",
        "-preset", "4",
        "-crf", "28",
        "-b:v", f"{bitrate_kbps}k",
        "-maxrate", f"{maxrate}k",
        "-bufsize", f"{bufsize}k",
        "-pix_fmt", "yuv420p10le",
        "-g", str(gop_size),
        "-keyint_min", str(gop_size),
        "-sc_threshold", "0",
        "-svtav1-params", "tune=0:enable-overlays=1:enable-tf=1:film-grain=8",
        
        # Opus Audio encoding
        "-c:a", "libopus",
        "-b:a", "96k",
        "-ac", "2",
        
        # HLS packaging with fragmented MP4
        "-hls_time", "4",
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", f"{short_name}_init.mp4",
        "-hls_playlist_type", "vod",
        "-hls_segment_filename", segments_pattern,
        
        # Output
        playlist_out
    ]
    
    logger.debug(f"Spawning FFmpeg command: {' '.join(cmd)}")
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"FFmpeg AV1 {short_name} processing failed.")
        # Only show last 500 chars of error to avoid log spam
        error_tail = process.stderr[-500:] if len(process.stderr) > 500 else process.stderr
        logger.error(f"FFmpeg Error output:\n{error_tail}")
        sys.exit(1)
    
    # Verify output
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.m4s"))
    init_file = Path(OUTPUT_DIR) / f"{short_name}_init.mp4"
    playlist_file = Path(OUTPUT_DIR) / f"{short_name}.m3u8"
    
    logger.info(f"  ✓ {short_name} complete: {len(generated_files)} segments, "
                f"init {'exists' if init_file.exists() else 'MISSING'}, "
                f"playlist {'exists' if playlist_file.exists() else 'MISSING'}")

def generate_master_manifest(active_profiles):
    """Assemble generated configuration profiles into a master playlist."""
    logger.info("Step 5/6: Generating master.m3u8 multi-bitrate playlist...")
    
    # HLS Version 6+ required for fMP4 segments
    master_content = "#EXTM3U\n#EXT-X-VERSION:7\n\n"
    
    for profile in active_profiles:
        # AV1 codec string: av01.profile.level.tier
        codec_string = "av01.0.08M.08,mp4a.40.2"
        
        master_content += (
            f"#EXT-X-STREAM-INF:"
            f"BANDWIDTH={profile['bandwidth']},"
            f"AVERAGE-BANDWIDTH={profile['avg_bandwidth']},"
            f"RESOLUTION={profile['res']},"
            f"FRAME-RATE={profile['fps']},"
            f"CODECS=\"{codec_string}\"\n"
            f"{profile['name']}.m3u8\n\n"
        )
    
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    with open(master_file_path, "w") as f:
        f.write(master_content)
    
    logger.info(f"Master playlist written with {len(active_profiles)} variants")

def upload_to_s3():
    """Sync processed assets to S3-compatible storage via MinIO Client."""
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Step 6/6: Uploading assets to {destination}...")
    
    cmd = ["mc", "cp", "-r", f"{OUTPUT_DIR}/", destination]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"MinIO Client upload failed: {process.stderr}")
        sys.exit(1)
    
    logger.info("✓ Upload phase successfully completed.")

def main():
    logger.info("=== STARTING AV1 VIDEO PACKAGING ENGINE ===")
    
    # Step 1: Verify environment
    check_dependencies()
    
    # Step 2: Analyze source
    width, height, native_bitrate, fps = get_input_metadata()
    source_bitrate_kbps = native_bitrate // 1000 if native_bitrate > 0 else 0
    
    # Step 3: Prepare output directory
    prepare_workspace()
    
    # Define resolution ladder with AV1-optimized bitrates
    all_possible_profiles = [
        {
            "name": "1080p",
            "scale": "1920:-2",
            "preset_bitrate": 3500,
            "res": "1920x1080",
            "threshold": 1080
        },
        {
            "name": "720p",
            "scale": "1280:-2",
            "preset_bitrate": 1800,
            "res": "1280x720",
            "threshold": 720
        },
        {
            "name": "480p",
            "scale": "854:-2",
            "preset_bitrate": 700,
            "res": "854x480",
            "threshold": 480
        }
    ]
    
    # Build active profiles based on source resolution
    active_profiles = []
    
    for profile in all_possible_profiles:
        if height >= profile["threshold"]:
            optimal_kbps = calculate_target_bitrate(
                source_bitrate_kbps,
                height,
                profile["threshold"],
                profile["preset_bitrate"]
            )
            
            active_profiles.append({
                "name": profile["name"],
                "scale": profile["scale"],
                "bitrate_kbps": optimal_kbps,
                "bandwidth": optimal_kbps * 1000,
                "avg_bandwidth": int(optimal_kbps * 1000 * 0.85),
                "res": profile["res"],
                "fps": fps
            })
    
    # Fallback for very low resolution sources
    if not active_profiles:
        logger.warning(f"Source height {height}p is below lowest profile threshold")
        fallback_kbps = min(source_bitrate_kbps, 400) if source_bitrate_kbps else 400
        fallback_kbps = max(fallback_kbps, 200)
        
        active_profiles.append({
            "name": "source",
            "scale": "trunc(iw/2)*2:trunc(ih/2)*2",
            "bitrate_kbps": fallback_kbps,
            "bandwidth": fallback_kbps * 1000,
            "avg_bandwidth": int(fallback_kbps * 1000 * 0.85),
            "res": f"{width}x{height}",
            "fps": fps
        })
    
    logger.info(f"Preparing to encode {len(active_profiles)} variants:")
    for p in active_profiles:
        logger.info(f"  - {p['name']}: {p['res']} @ {p['bitrate_kbps']} Kbps")
    
    # Step 4: Encode all variants
    for profile in active_profiles:
        run_ffmpeg_transcode(
            profile["scale"],
            profile["bitrate_kbps"],
            profile["name"],
            fps
        )
    
    # Step 5: Generate manifest
    generate_master_manifest(active_profiles)
    
    # Step 6: Upload
    upload_to_s3()
    
    logger.info("=== WORKFLOW PIPELINE COMPLETED SUCCESSFULLY ===")
    logger.info(f"Stream URL: https://local.test/video-streams/{VIDEO_ID}/master.m3u8")

if __name__ == "__main__":
    main()
