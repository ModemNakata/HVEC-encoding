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
MC_ALIAS_PATH = "local_s3/video-streams2" # Your bucket target path

# Configure verbose logging format
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("video_pipeline.log", mode="w")
    ]
)
logger = logging.getLogger("HLS-Pipeline")

def check_dependencies():
    """Verify system prerequisites exist before running CPU intensive tasks."""
    logger.info("Step 1/6: Checking system dependencies...")
    
    # Added ffprobe to the system tool validation list
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
    logger.info("All dependencies and input files successfully verified.")

def get_input_resolution():
    """Use ffprobe to pull metadata parameters and parse native height/width dimensions."""
    logger.info("Step 2/6: Inspecting input video properties via ffprobe...")
    
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height", 
        "-of", "json", 
        INPUT_VIDEO
    ]
    
    logger.debug(f"Spawning inspection command: {' '.join(cmd)}")
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error("Failed to parse video attributes with ffprobe.")
        logger.error(f"ffprobe trace:\n{process.stderr}")
        sys.exit(1)
        
    try:
        metadata = json.loads(process.stdout)
        width = metadata["streams"][0]["width"]
        height = metadata["streams"][0]["height"]
        logger.info(f" NATIVE QUALITY DETECTED: {width}x{height} ({height}p)")
        return width, height
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"Failed to read JSON payload returned by ffprobe. Error: {e}")
        sys.exit(1)

def prepare_workspace():
    """Create a clean workspace environment, wiping out stale artifacts if necessary."""
    logger.info(f"Step 3/6: Preparing local directory staging workspace...")
    if os.path.exists(OUTPUT_DIR):
        logger.warning(f"Stale workspace detected at '{OUTPUT_DIR}'. Wiping folder clean...")
        shutil.rmtree(OUTPUT_DIR)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger.debug(f"Created fresh staging directory layout at: {os.path.abspath(OUTPUT_DIR)}")

def run_ffmpeg_transcode(scale_filter, bitrate, short_name):
    """Execute a single FFmpeg transcoding sub-process thread wrapper."""
    logger.info(f"Executing HLS conversion mapping thread for {short_name}...")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.ts")
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-vf", f"scale={scale_filter}",
        "-c:v", "libx264", "-b:v", bitrate,
        "-g", "60",                  # Force keyframe every 60 frames (assuming 30fps)
        "-hls_time", "4",            # Cut into strict 4-second blocks
        "-hls_playlist_type", "vod", # Set VOD attributes
        "-hls_segment_filename", segments_pattern,
        playlist_out
    ]
    
    logger.debug(f"Spawning raw command array: {' '.join(cmd)}")
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"FFmpeg {short_name} processing failed with exit code: {process.returncode}")
        logger.error(f"FFmpeg Error output trace:\n{process.stderr}")
        sys.exit(1)
    
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.ts"))
    logger.info(f" Finished {short_name} layer generation. Created {len(generated_files)} video chunks.")

def generate_master_manifest(active_profiles):
    """Assemble generated configuration profiles into a dynamic layout string."""
    logger.info("Step 5/6: Generating core master.m3u8 dynamic multi-bitrate roadmap file...")
    
    master_content = "#EXTM3U\n#EXT-X-VERSION:3\n\n"
    
    for profile in active_profiles:
        master_content += f"#EXT-X-STREAM-INF:BANDWIDTH={profile['bandwidth']},RESOLUTION={profile['res']}\n"
        master_content += f"{profile['name']}.m3u8\n\n"
        
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    with open(master_file_path, "w") as f:
        f.write(master_content)
        
    logger.debug(f"Dynamic master manifest layout successfully written directly to: {master_file_path}")

def upload_to_rustfs():
    """Sync raw local asset slices directly to the target RustFS storage bucket via the MinIO Client."""
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Step 6/6: Uploading assets tracking manifest to local S3 storage bucket...")
    
    cmd = ["mc", "cp", "-r", f"{OUTPUT_DIR}/", destination]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"MinIO Client upload failed with status code: {process.returncode}")
        logger.error(f"Client upload log stack trace:\n{process.stderr}")
        sys.exit(1)
        
    logger.info(" Upload phase successfully executed with no object drop faults.")

def main():
    logger.info("=== STARTING VIDEO PACKAGING ENGINE AND TRANSLATION AGENT ===")
    
    check_dependencies()
    _, native_height = get_input_resolution()
    prepare_workspace()
    
    # Define our structural encoding definitions matrix
    all_possible_profiles = [
        {"name": "1080p", "scale": "1920:-2", "bitrate": "5000k", "bandwidth": 5000000, "res": "1920x1080", "threshold": 1080},
        {"name": "720p",  "scale": "1280:-2", "bitrate": "2500k", "bandwidth": 2500000, "res": "1280x720",  "threshold": 720},
        {"name": "480p",  "scale": "854:-2",  "bitrate": "1000k", "bandwidth": 1000000, "res": "854x480",  "threshold": 480}
    ]
    
    # Filter the list on the fly based on what the video actually supports
    active_profiles = [p for p in all_possible_profiles if native_height >= p["threshold"]]
    
    # Catch edge case: If the video is extremely low resolution (e.g., 360p or smaller)
    if not active_profiles:
        logger.warning(f"Input video quality ({native_height}p) sits below normal ladders. Forcing original size fallback processing.")
        active_profiles.append({
            "name": "fallback", "scale": "trunc(iw/2)*2:trunc(ih/2)*2", "bitrate": "600k", "bandwidth": 600000, "res": f"scaled_native", "threshold": 0
        })

    logger.info(f"Step 4/6: Initiating filtered transcoding pipeline matrix...")
    logger.info(f"Targeting outputs: {[p['name'] for p in active_profiles]}")
    
    for profile in active_profiles:
        run_ffmpeg_transcode(profile["scale"], profile["bitrate"], profile["name"])
        
    generate_master_manifest(active_profiles)
    upload_to_rustfs()
    
    logger.info("=== WORKFLOW PIPELINE COMPLETED SUCCESSFULLY ===")
    logger.info(f"Vidstack Endpoint Target URL: http://192.168.1.188:9000/video-streams/{VIDEO_ID}/master.m3u8")

if __name__ == "__main__":
    main()


# # If you want pristine, higher-quality streaming at the cost of more storage:
# {"name": "1080p", "scale": "1920:-2", "bitrate": "8000k", "bandwidth": 8000000, ...},
# {"name": "720p",  "scale": "1280:-2", "bitrate": "4000k", "bandwidth": 4000000, ...},
