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
VIDEO_ID = "video-hevc"                  # Unique ID for the bucket folder
OUTPUT_DIR = "my_processed_video_hevc"   # Local staging folder
MC_ALIAS_PATH = "local_s3/video-streams" # Your bucket target path

# Configure clean logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("HEVC-fMP4-Clean-Pipeline")

def check_dependencies():
    """Verify system prerequisites exist before running calculations."""
    logger.info("Step 1/7: Checking system dependencies...")
    
    for tool in ["ffmpeg", "ffprobe", "mc"]:
        path = shutil.which(tool)
        if not path:
            logger.error(f"Missing required tool: '{tool}'. Please install it and add it to your PATH.")
            sys.exit(1)
            
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"Input file '{INPUT_VIDEO}' not found in the current directory.")
        sys.exit(1)
        
    ffmpeg_check = subprocess.run(["ffmpeg", "-encoders"], stdout=subprocess.PIPE, text=True)
    if "libx265" not in ffmpeg_check.stdout:
        logger.error("Your system FFmpeg build lacks the 'libx265' HEVC encoder module.")
        sys.exit(1)

    logger.info("All dependencies and HEVC system modules successfully verified.")

def get_input_metadata():
    """Use ffprobe to extract parameters and explicitly print the initial bitrate."""
    logger.info("Step 2/7: Inspecting input video properties via ffprobe...")
    
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
        
        print("\n" + "="*60)
        print(f" INITIAL BITRATE PROBE RESULT:")
        print(f" Source File:     {INPUT_VIDEO}")
        print(f" Resolution:      {width}x{height} ({height}p)")
        if native_bitrate > 0:
            print(f" Raw Bitrate:     {native_bitrate} bits/sec")
            print(f" Readable Speed:  {native_bitrate // 1000} kbps (~{native_bitrate / 1_000_000:.2f} Mbps)")
        else:
            print(f" Raw Bitrate:     Could not be determined accurately from file headers.")
        print("="*60 + "\n")
            
        return width, height, native_bitrate
    except (KeyError, IndexError, json.JSONDecodeError):
        logger.error("Failed to parse JSON payload returned by ffprobe.")
        sys.exit(1)

def purge_remote_workspace():
    """CRITICAL FIX: Wipe out the target S3 path completely before encoding to prevent chunk collisions."""
    target_bucket_path = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Step 3/7: Purging remote S3 directory to clear stale artifacts: {target_bucket_path}")
    
    # mc rm --recursive --force drops everything under this specific video sub-folder prefix safely
    cmd = ["mc", "rm", "--recursive", "--force", target_bucket_path]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode == 0:
        logger.info(" Remote S3 workspace successfully wiped clean.")
    else:
        logger.warning(f"S3 purge reported a non-zero exit code. Proceeding anyway (bucket may have been empty).")

def prepare_local_workspace():
    """Create a clean local staging environment, wiping out local stale artifacts."""
    logger.info(f"Step 4/7: Preparing local directory staging workspace...")
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def run_ffmpeg_transcode(scale_filter, bitrate, short_name):
    """Execute a single HEVC transcoding subprocess thread converting to fMP4 HLS."""
    logger.info(f"Executing HEVC fMP4 HLS conversion thread for {short_name} at {bitrate}...")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.m4s") 
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-vf", f"scale={scale_filter}",
        "-c:v", "libx265",             
        "-preset", "fast",             
        "-vtag", "hvc1",               # Required for Safari/iOS support
        "-b:v", bitrate,             
        "-pix_fmt", "yuv420p",         
        "-g", "60",                  
        "-hls_time", "4",            
        "-hls_segment_type", "fmp4",   
        "-hls_fmp4_init_filename", f"{short_name}_init.mp4", 
        "-hls_playlist_type", "vod", 
        "-hls_segment_filename", segments_pattern,
        playlist_out
    ]
    
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error(f"FFmpeg HEVC {short_name} processing failed.")
        logger.error(f"FFmpeg Trace Log:\n{process.stderr}")
        sys.exit(1)
    
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.m4s")) 
    logger.info(f" Finished HEVC {short_name} layer generation. Created {len(generated_files)} video fragments.")

def generate_master_manifest(active_profiles):
    """Assemble generated configuration profiles into a dynamic layout string."""
    logger.info("Step 6/7: Generating core master.m3u8 layout...")
    
    master_content = "#EXTM3U\n#EXT-X-VERSION:6\n\n"
    
    for profile in active_profiles:
        master_content += f"#EXT-X-STREAM-INF:BANDWIDTH={profile['bandwidth']},RESOLUTION={profile['res']},CODECS=\"hvc1.1.6.L120.90\"\n"
        master_content += f"{profile['name']}.m3u8\n\n"
        
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    with open(master_file_path, "w") as f:
        f.write(master_content)

def upload_to_rustfs():
    """Sync raw local asset slices directly to the target storage bucket via the MinIO Client."""
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Step 7/7: Uploading crisp assets to storage bucket...")
    
    cmd = ["mc", "cp", "-r", f"{OUTPUT_DIR}/", destination]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    if process.returncode != 0:
        logger.error("MinIO Client upload failed.")
        sys.exit(1)
    logger.info(" Upload phase successfully executed.")

def main():
    logger.info("=== STARTING CLEAN-SLATE HEVC PACKAGING ENGINE ===")
    
    check_dependencies()
    _, native_height, native_bitrate = get_input_metadata()
    
    # Wipe remote S3 and local folders completely bare before processing vectors
    purge_remote_workspace()
    prepare_local_workspace()
    
    all_possible_profiles = [
        {"name": "1080p", "scale": "1920:-2", "default_bitrate": 3200, "res": "1920x1080", "threshold": 1080}, 
        {"name": "720p",  "scale": "1280:-2", "default_bitrate": 1800, "res": "1280x720",  "threshold": 720},  
        {"name": "480p",  "scale": "854:-2",  "default_bitrate": 800,  "res": "854x480",  "threshold": 480}   
    ]
    
    active_profiles = []
    native_kbps = native_bitrate // 1000 if native_bitrate > 0 else None
    last_assigned_bitrate = 999999  
    
    logger.info(f"Step 5/7: Evaluating target profile constraints...")
    for p in all_possible_profiles:
        if native_height >= p["threshold"]:
            if native_kbps and native_kbps < p["default_bitrate"]:
                actual_kbps = max(250, int(native_kbps * 0.80))
            else:
                actual_kbps = p["default_bitrate"]
            
            if actual_kbps >= last_assigned_bitrate:
                actual_kbps = int(last_assigned_bitrate * 0.70)
                
            last_assigned_bitrate = actual_kbps
                
            active_profiles.append({
                "name": p["name"], "scale": p["scale"], "bitrate": f"{actual_kbps}k", "bandwidth": actual_kbps * 1000, "res": p["res"]
            })
            
    if not active_profiles:
        fallback_kbps = int(native_kbps * 0.75) if native_kbps else 500
        fallback_kbps = max(200, fallback_kbps)
        active_profiles.append({
            "name": "fallback", "scale": "trunc(iw/2)*2:trunc(ih/2)*2", "bitrate": f"{fallback_kbps}k", "bandwidth": fallback_kbps * 1000, "res": "scaled_native"
        })
        
    logger.info(f"Executing filtered transcoding pipeline matrix: {[p['name'] for p in active_profiles]}")
    for profile in active_profiles:
        run_ffmpeg_transcode(profile["scale"], profile["bitrate"], profile["name"])
        
    generate_master_manifest(active_profiles)
    upload_to_rustfs()
    
    logger.info("=== WORKFLOW PIPELINE COMPLETED SUCCESSFULLY ===")
    logger.info(f"Vidstack Endpoint Target URL: https://local.test/video-streams/{VIDEO_ID}/master.m3u8")

if __name__ == "__main__":
    main()
