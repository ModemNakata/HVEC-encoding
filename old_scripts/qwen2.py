#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
import logging
from pathlib import Path
from datetime import datetime

# ==========================================
# CONFIGURATION
# ==========================================
INPUT_VIDEO = "video_output.mp4"         # The raw source file
VIDEO_ID = "video-xyz"                   # Unique ID for the bucket folder
OUTPUT_DIR = "my_processed_video"        # Local staging folder
MC_ALIAS_PATH = "local_s3/video-streams2" # Your bucket target path

# Quality Settings
CRF_VALUE = 18                           # Visually lossless quality
PRESET = "slow"                          # Better compression efficiency
AUDIO_CODEC = "aac"                      # Audio codec
AUDIO_BITRATE = "192k"                   # High-quality audio

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

def run_command(cmd, description="Command"):
    """Execute a command and log both stdout and stderr extensively."""
    logger.info(f"{'='*60}")
    logger.info(f"EXECUTING: {description}")
    logger.info(f"Command: {' '.join(cmd)}")
    logger.info(f"{'='*60}")
    
    try:
        process = subprocess.run(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True,
            timeout=7200  # 2 hour timeout
        )
        
        if process.stdout and process.stdout.strip():
            logger.info(f"STDOUT OUTPUT:\n{process.stdout}")
        if process.stderr and process.stderr.strip():
            logger.info(f"STDERR OUTPUT:\n{process.stderr}")
        
        if process.returncode != 0:
            logger.error(f"❌ COMMAND FAILED with exit code: {process.returncode}")
            return False, process.stderr
        
        logger.info(f"✅ COMMAND COMPLETED SUCCESSFULLY")
        return True, process.stdout
        
    except subprocess.TimeoutExpired:
        logger.error(f"❌ COMMAND TIMED OUT after 2 hours")
        return False, "Timeout expired"
    except Exception as e:
        logger.error(f"❌ EXCEPTION: {str(e)}")
        return False, str(e)

def check_dependencies():
    logger.info("\n" + "="*60)
    logger.info("STEP 1/7: CHECKING SYSTEM DEPENDENCIES")
    logger.info("="*60)
    for tool in ["ffmpeg", "ffprobe", "mc"]:
        if not shutil.which(tool):
            logger.error(f"❌ Missing required tool: '{tool}'")
            sys.exit(1)
        logger.info(f"✅ Found '{tool}'")
    
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"❌ Input file '{INPUT_VIDEO}' not found.")
        sys.exit(1)
    logger.info(f"✅ Input file verified: {INPUT_VIDEO}")

def get_input_properties():
    """Get resolution and TOTAL bitrate of the input file."""
    logger.info("\n" + "="*60)
    logger.info("STEP 2/7: INSPECTING INPUT VIDEO PROPERTIES")
    logger.info("="*60)
    
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,bit_rate", 
        "-show_entries", "format=bit_rate", 
        "-of", "json", 
        INPUT_VIDEO
    ]
    
    success, output = run_command(cmd, "Inspecting video metadata and bitrate")
    if not success: sys.exit(1)
    
    try:
        metadata = json.loads(output)
        stream = metadata["streams"][0]
        fmt = metadata["format"]
        
        width = stream["width"]
        height = stream["height"]
        
        # Try to get stream bitrate first, fall back to format bitrate
        bit_rate = stream.get("bit_rate")
        if not bit_rate:
            bit_rate = fmt.get("bit_rate")
        
        # Convert to integer, handle cases where it might be 'N/A'
        total_bitrate_bps = int(bit_rate) if bit_rate and bit_rate != "N/A" else 0
        total_bitrate_kbps = total_bitrate_bps / 1000
        
        logger.info(f"📹 Resolution: {width}x{height} ({height}p)")
        logger.info(f"📊 Total Input Bitrate: {total_bitrate_kbps:.0f} kbps ({total_bitrate_bps} bps)")
        
        return width, height, total_bitrate_kbps
        
    except Exception as e:
        logger.error(f"❌ Failed to parse properties: {e}")
        sys.exit(1)

def prepare_workspace():
    logger.info("\n" + "="*60)
    logger.info("STEP 3/7: PREPARING LOCAL WORKSPACE")
    logger.info("="*60)
    if os.path.exists(OUTPUT_DIR):
        logger.warning(f"⚠️  Cleaning old workspace: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger.info(f"✅ Fresh workspace created at: {os.path.abspath(OUTPUT_DIR)}")

def clear_bucket_folder():
    logger.info("\n" + "="*60)
    logger.info("STEP 4/7: CLEARING BUCKET CONTENTS")
    logger.info("="*60)
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    list_cmd = ["mc", "ls", destination]
    success, output = run_command(list_cmd, "Checking bucket contents")
    
    if success and output and output.strip():
        logger.info(f"⚠️  Clearing existing files in {destination}")
        rm_cmd = ["mc", "rm", "--recursive", "--force", destination]
        run_command(rm_cmd, "Removing remote files")
    else:
        logger.info(f"✅ Bucket folder is empty or new.")

def run_ffmpeg_transcode(scale_filter, short_name, profile_info, input_total_bitrate):
    """High-Quality Capped CRF Transcoding with Dynamic Bitrate Limits."""
    logger.info("\n" + "-"*60)
    logger.info(f"TRANSCODING: {profile_info['name']} (Quality-First Mode)")
    logger.info(f"{'-'*60}")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.m4s")
    
    # DYNAMIC BITRATE CAPPING LOGIC
    # We want to ensure we don't exceed the source's total bitrate.
    # We also subtract the audio bitrate to get the max video bitrate.
    audio_br_kbps = int(AUDIO_BITRATE.replace('k', ''))
    
    # Calculate a safe max video bitrate based on input
    # We use 90% of the input total bitrate to ensure we definitely save space/improve efficiency
    safe_max_total = input_total_bitrate * 0.9 
    
    # Ensure the max isn't lower than a reasonable minimum for the resolution
    min_bitrates = {"1080p": 2000, "720p": 1000, "480p": 500}
    min_br = min_bitrates.get(short_name, 500)
    
    calculated_max = max(safe_max_total - audio_br_kbps, min_br)
    max_rate_str = f"{int(calculated_max)}k"
    buffer_size_str = f"{int(calculated_max * 2)}k"

    logger.info(f"   Input Total Bitrate: {input_total_bitrate:.0f}k")
    logger.info(f"   Calculated Max Video Rate: {max_rate_str} (Capped to avoid bloating)")
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-vf", f"scale={scale_filter}",
        "-c:v", "libx265",
        "-crf", str(CRF_VALUE),       # High quality target
        "-preset", PRESET,             # Slow preset for better efficiency
        "-maxrate", max_rate_str,      # Dynamic hard ceiling
        "-bufsize", buffer_size_str,   # Buffer for dynamic allocation
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-g", "60",
        "-sc_threshold", "0",
        "-hls_time", "4",
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",   # Safari-compatible fMP4
        "-hls_fmp4_init_filename", f"{short_name}_init.mp4",
        "-hls_segment_filename", segments_pattern,
        "-hls_flags", "independent_segments+program_date_time",
        playlist_out
    ]
    
    start_time = datetime.now()
    logger.info(f"⏱️  Starting high-quality encode...")
    success, output = run_command(cmd, f"H.265 Capped-CRF for {short_name}")
    
    if not success:
        logger.error(f"❌ Transcode failed for {short_name}")
        sys.exit(1)
        
    duration = (datetime.now() - start_time).total_seconds()
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.m4s"))
    total_size = sum(f.stat().st_size for f in generated_files)
    
    logger.info(f"✅ {short_name} completed in {duration:.2f}s")
    logger.info(f"   Segments: {len(generated_files)} | Total Size: {total_size/(1024*1024):.2f} MB")

def generate_master_manifest(active_profiles):
    logger.info("\n" + "="*60)
    logger.info("STEP 5/7: GENERATING MASTER MANIFEST")
    logger.info("="*60)
    master_content = "#EXTM3U\n#EXT-X-VERSION:7\n\n"
    for profile in active_profiles:
        master_content += f"#EXT-X-STREAM-INF:BANDWIDTH={profile['bandwidth']},RESOLUTION={profile['res']}\n"
        master_content += f"{profile['name']}.m3u8\n\n"
        
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    with open(master_file_path, "w") as f:
        f.write(master_content)
    logger.info(f"✅ Master manifest created at: {master_file_path}")

def upload_to_rustfs():
    logger.info("\n" + "="*60)
    logger.info("STEP 6/7: UPLOADING TO RUSTFS/MINIO")
    logger.info("="*60)
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    cmd = ["mc", "cp", "--recursive", f"{OUTPUT_DIR}/", destination]
    success, _ = run_command(cmd, "Uploading to bucket")
    if not success: sys.exit(1)
    
    # Verify
    verify_cmd = ["mc", "ls", "--recursive", destination]
    run_command(verify_cmd, "Verifying remote files")

def main():
    logger.info("\n" + "#"*60)
    logger.info("# STARTING HIGH-QUALITY HLS PIPELINE")
    logger.info("#"*60)
    logger.info(f"Settings: CRF={CRF_VALUE}, Preset={PRESET}")
    
    check_dependencies()
    native_width, native_height, input_bitrate = get_input_properties()
    prepare_workspace()
    clear_bucket_folder()
    
    # Profiles with bandwidth estimates
    all_possible_profiles = [
        {"name": "1080p", "scale": "1920:-2", "bandwidth": 6000000, "res": "1920x1080", "threshold": 1080},
        {"name": "720p",  "scale": "1280:-2", "bandwidth": 3500000, "res": "1280x720",  "threshold": 720},
        # {"name": "480p",  "scale": "854:-2",  "bandwidth": 1500000, "res": "854x480",  "threshold": 480}
    ]
    
    active_profiles = [p for p in all_possible_profiles if native_height >= p["threshold"]]
    if not active_profiles:
        active_profiles.append({"name": "fallback", "scale": "trunc(iw/2)*2:trunc(ih/2)*2", "bandwidth": 800000, "res": "native", "threshold": 0})

    logger.info(f"\n>>> Encoding {len(active_profiles)} profiles...")
    for profile in active_profiles:
        # Pass the input_bitrate to the transcode function
        run_ffmpeg_transcode(profile["scale"], profile["name"], profile, input_bitrate)
        
    generate_master_manifest(active_profiles)
    upload_to_rustfs()
    
    logger.info("\n" + "#"*60)
    logger.info("# ✅ HIGH-QUALITY PIPELINE COMPLETED")
    logger.info("#"*60)
    logger.info(f"URL: http://192.168.1.188:9000/video-streams2/{VIDEO_ID}/master.m3u8")

if __name__ == "__main__":
    main()
