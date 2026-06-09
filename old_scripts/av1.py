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
        
    ffmpeg_check = subprocess.run(["ffmpeg", "-encoders"], stdout=subprocess.PIPE, text=True)
    if "libsvtav1" not in ffmpeg_check.stdout:
        logger.error("Your system FFmpeg build lacks the 'libsvtav1' encoder plugin module.")
        sys.exit(1)

    logger.info("All dependencies and AV1 system modules successfully verified.")

def get_input_metadata():
    """Use ffprobe to pull video parameters (dimensions and bitrates)."""
    logger.info("Step 2/6: Inspecting input video properties via ffprobe...")
    
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,bit_rate", 
        "-show_entries", "format=bit_rate",
        "-of", "json", 
        INPUT_VIDEO
    ]
    
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if process.returncode != 0:
        logger.error("Failed to parse video attributes with ffprobe.")
        sys.exit(1)
        
    try:
        metadata = json.loads(process.stdout)
        width = metadata["streams"][0]["width"]
        height = metadata["streams"][0]["height"]
        
        bitrate_str = metadata["streams"][0].get("bit_rate")
        if not bitrate_str or bitrate_str == "N/A":
            bitrate_str = metadata.get("format", {}).get("bit_rate", "0")
            
        native_bitrate = int(bitrate_str)
        return width, height, native_bitrate
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.error("Failed to read JSON payload returned by ffprobe.")
        sys.exit(1)

def prepare_workspace():
    """Create a clean workspace environment, wiping out stale artifacts if necessary."""
    logger.info(f"Step 3/6: Preparing local directory staging workspace...")
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_ffmpeg_transcode(scale_filter, bitrate, short_name):
    """Execute a single AV1 transcoding subprocess thread converting to fMP4 HLS."""
    logger.info(f"Executing AV1 fMP4 HLS conversion mapping thread for {short_name} at {bitrate}...")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.m4s") # FIXED: Changed from .ts to .m4s
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-vf", f"scale={scale_filter}",
        "-c:v", "libsvtav1",         
        "-preset", "8",               
        "-b:v", bitrate,             
        "-pix_fmt", "yuv420p",        
        "-g", "60",                  
        "-hls_time", "4",            
        "-hls_segment_type", "fmp4",  # FIXED: Forces Fragmented MP4 containers required for AV1 web streaming
        "-hls_fmp4_init_filename", f"{short_name}_init.mp4", # FIXED: Prevents variant filename collision
        "-hls_playlist_type", "vod", 
        "-hls_segment_filename", segments_pattern,
        playlist_out
    ]
    
    logger.debug(f"Spawning raw command array: {' '.join(cmd)}")
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"FFmpeg AV1 {short_name} processing failed.")
        logger.error(f"FFmpeg Error output trace:\n{process.stderr}")
        sys.exit(1)
    
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.m4s")) # FIXED: Scan for tracking files properly
    logger.info(f" Finished AV1 {short_name} layer generation. Created {len(generated_files)} video fragments.")

def generate_master_manifest(active_profiles):
    """Assemble generated configuration profiles into a dynamic layout string."""
    logger.info("Step 5/6: Generating core master.m3u8 dynamic multi-bitrate roadmap file...")
    
    # Bumped HLS standard specification target to Version 6 for optimal fMP4 playback support
    master_content = "#EXTM3U\n#EXT-X-VERSION:6\n\n"
    
    for profile in active_profiles:
        # Explicitly declaring the AV1 codec string helps the browser decode accurately on the fly
        master_content += f"#EXT-X-STREAM-INF:BANDWIDTH={profile['bandwidth']},RESOLUTION={profile['res']},CODECS=\"av01.0.08M.08\"\n"
        master_content += f"{profile['name']}.m3u8\n\n"
        
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    with open(master_file_path, "w") as f:
        f.write(master_content)

def upload_to_rustfs():
    """Sync raw local asset slices directly to the target RustFS storage bucket via the MinIO Client."""
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Step 6/6: Uploading assets tracking manifest to local S3 storage bucket...")
    
    cmd = ["mc", "cp", "-r", f"{OUTPUT_DIR}/", destination]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error("MinIO Client upload failed.")
        sys.exit(1)
    logger.info(" Upload phase successfully executed with no object drop faults.")

def main():
    logger.info("=== STARTING AV1 VIDEO PACKAGING ENGINE ===")
    
    check_dependencies()
    _, native_height, native_bitrate = get_input_metadata()
    prepare_workspace()
    
    all_possible_profiles = [
        {"name": "1080p", "scale": "1920:-2", "default_bitrate": 3500, "res": "1920x1080", "threshold": 1080}, 
        {"name": "720p",  "scale": "1280:-2", "default_bitrate": 1800, "res": "1280x720",  "threshold": 720},  
        {"name": "480p",  "scale": "854:-2",  "default_bitrate": 700,  "res": "854x480",  "threshold": 480}   
    ]
    
    active_profiles = []
    native_kbps = native_bitrate // 1000 if native_bitrate > 0 else None
    
    for p in all_possible_profiles:
        if native_height >= p["threshold"]:
            if native_kbps and native_kbps < p["default_bitrate"]:
                actual_kbps = max(200, int(native_kbps * 0.75))
            else:
                actual_kbps = p["default_bitrate"]
                
            active_profiles.append({
                "name": p["name"], "scale": p["scale"], "bitrate": f"{actual_kbps}k", "bandwidth": actual_kbps * 1000, "res": p["res"]
            })
            
    if not active_profiles:
        fallback_kbps = int(native_kbps * 0.7) if native_kbps else 400
        fallback_kbps = max(150, fallback_kbps)
        active_profiles.append({
            "name": "fallback", "scale": "trunc(iw/2)*2:trunc(ih/2)*2", "bitrate": f"{fallback_kbps}k", "bandwidth": fallback_kbps * 1000, "res": "scaled_native"
        })
        
    for profile in active_profiles:
        run_ffmpeg_transcode(profile["scale"], profile["bitrate"], profile["name"])
        
    generate_master_manifest(active_profiles)
    upload_to_rustfs()
    
    logger.info("=== WORKFLOW PIPELINE COMPLETED SUCCESSFULLY ===")
    logger.info(f"Vidstack Endpoint Target URL: https://local.test/video-streams/{VIDEO_ID}/master.m3u8")

if __name__ == "__main__":
    main()
