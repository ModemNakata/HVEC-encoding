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

# H.265 Encoding Settings
CRF_VALUE = 23                           # Quality factor (18=high, 23=default, 28=smaller)
PRESET = "medium"                        # Encoding speed vs compression: ultrafast, fast, medium, slow, slower
AUDIO_CODEC = "aac"                      # Audio codec: aac (best compatibility), opus (modern)
AUDIO_BITRATE = "128k"                   # Audio bitrate

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
            timeout=3600  # 1 hour timeout per command
        )
        
        # Log stdout if present
        if process.stdout and process.stdout.strip():
            logger.info(f"STDOUT OUTPUT:\n{process.stdout}")
        
        # Log stderr (FFmpeg outputs progress to stderr)
        if process.stderr and process.stderr.strip():
            logger.info(f"STDERR OUTPUT:\n{process.stderr}")
        
        # Check return code
        if process.returncode != 0:
            logger.error(f"❌ COMMAND FAILED with exit code: {process.returncode}")
            logger.error(f"Error details:\n{process.stderr}")
            return False, process.stderr
        
        logger.info(f"✅ COMMAND COMPLETED SUCCESSFULLY (exit code: {process.returncode})")
        return True, process.stdout
        
    except subprocess.TimeoutExpired:
        logger.error(f"❌ COMMAND TIMED OUT after 1 hour")
        return False, "Timeout expired"
    except Exception as e:
        logger.error(f"❌ EXCEPTION during command execution: {str(e)}")
        return False, str(e)

def check_dependencies():
    """Verify system prerequisites exist before running CPU intensive tasks."""
    logger.info("\n" + "="*60)
    logger.info("STEP 1/7: CHECKING SYSTEM DEPENDENCIES")
    logger.info("="*60)
    
    required_tools = ["ffmpeg", "ffprobe", "mc"]
    all_found = True
    
    for tool in required_tools:
        path = shutil.which(tool)
        if path:
            logger.info(f"✅ Found '{tool}' at: {path}")
            
            # Get version info for ffmpeg/ffprobe
            if tool in ["ffmpeg", "ffprobe"]:
                version_cmd = [tool, "-version"]
                success, output = run_command(version_cmd, f"Getting {tool} version")
                if success and output:
                    # Extract first line with version info
                    first_line = output.split('\n')[0] if output else "Unknown version"
                    logger.info(f"   Version info: {first_line}")
        else:
            logger.error(f"❌ Missing required tool: '{tool}'")
            logger.error(f"   Please install it and ensure it's in your PATH")
            all_found = False
            
    if not all_found:
        logger.error("\n❌ DEPENDENCY CHECK FAILED - Cannot proceed without all required tools")
        sys.exit(1)
    
    if not os.path.exists(INPUT_VIDEO):
        logger.error(f"❌ Input file '{INPUT_VIDEO}' not found in current directory: {os.getcwd()}")
        logger.error(f"   Current directory contents: {os.listdir('.')}")
        sys.exit(1)
    
    # Get input file size
    file_size = os.path.getsize(INPUT_VIDEO)
    size_mb = file_size / (1024 * 1024)
    logger.info(f"✅ Input file verified: {INPUT_VIDEO} ({size_mb:.2f} MB)")
    logger.info("✅ All dependencies and input files successfully verified.\n")

def get_input_resolution():
    """Use ffprobe to pull metadata parameters and parse native height/width dimensions."""
    logger.info("\n" + "="*60)
    logger.info("STEP 2/7: INSPECTING INPUT VIDEO PROPERTIES")
    logger.info("="*60)
    
    cmd = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,duration", 
        "-of", "json", 
        INPUT_VIDEO
    ]
    
    success, output = run_command(cmd, "Inspecting video metadata with ffprobe")
    
    if not success:
        logger.error("❌ Failed to parse video attributes with ffprobe.")
        sys.exit(1)
        
    try:
        metadata = json.loads(output)
        stream = metadata["streams"][0]
        
        width = stream["width"]
        height = stream["height"]
        fps = stream.get("r_frame_rate", "unknown")
        codec = stream.get("codec_name", "unknown")
        duration = stream.get("duration", "unknown")
        
        logger.info(f"📹 VIDEO PROPERTIES DETECTED:")
        logger.info(f"   Resolution: {width}x{height} ({height}p)")
        logger.info(f"   Frame Rate: {fps}")
        logger.info(f"   Codec: {codec}")
        logger.info(f"   Duration: {duration} seconds")
        
        return width, height
        
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"❌ Failed to read JSON payload from ffprobe. Error: {e}")
        logger.error(f"Raw output:\n{output}")
        sys.exit(1)

def prepare_workspace():
    """Create a clean workspace environment, wiping out stale artifacts if necessary."""
    logger.info("\n" + "="*60)
    logger.info("STEP 3/7: PREPARING LOCAL WORKSPACE")
    logger.info("="*60)
    
    abs_output_dir = os.path.abspath(OUTPUT_DIR)
    
    if os.path.exists(OUTPUT_DIR):
        logger.warning(f"⚠️  Stale workspace detected at: {abs_output_dir}")
        logger.info(f"   Removing old directory and all contents...")
        
        try:
            shutil.rmtree(OUTPUT_DIR)
            logger.info(f"✅ Successfully removed old workspace")
        except Exception as e:
            logger.error(f"❌ Failed to remove old workspace: {e}")
            sys.exit(1)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logger.info(f"✅ Created fresh staging directory: {abs_output_dir}")
    logger.info(f"   Directory exists: {os.path.exists(OUTPUT_DIR)}")
    logger.info(f"   Is writable: {os.access(OUTPUT_DIR, os.W_OK)}\n")

def clear_bucket_folder():
    """Clear all existing files in the target bucket folder before uploading."""
    logger.info("\n" + "="*60)
    logger.info("STEP 4/7: CLEARING EXISTING BUCKET CONTENTS")
    logger.info("="*60)
    
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Target bucket path: {destination}")
    
    # First, check if the folder exists by listing it
    list_cmd = ["mc", "ls", destination]
    logger.info(f"Checking if bucket folder exists...")
    success, output = run_command(list_cmd, f"Listing bucket contents at {destination}")
    
    if not success:
        logger.warning(f"⚠️  Bucket folder may not exist yet or is empty: {destination}")
        logger.info(f"   This is fine - we'll create it during upload")
        logger.info(f"✅ Bucket cleanup step completed (nothing to clear)\n")
        return
    
    # If folder exists and has content, remove it
    if output and output.strip():
        logger.info(f"⚠️  Existing content found in bucket folder")
        logger.info(f"   Contents:\n{output}")
        logger.info(f"   Removing all existing files...")
        
        # Use mc rm --recursive --force to delete everything
        rm_cmd = ["mc", "rm", "--recursive", "--force", destination]
        success, output = run_command(rm_cmd, f"Removing all files from {destination}")
        
        if success:
            logger.info(f"✅ Successfully cleared all existing content from bucket folder")
        else:
            logger.error(f"❌ Failed to clear bucket folder")
            logger.error(f"   You may need to manually verify/clean: {destination}")
            # Don't exit here - we can still try to upload
    else:
        logger.info(f"✅ Bucket folder is already empty or doesn't exist\n")

def run_ffmpeg_transcode(scale_filter, short_name, profile_info):
    """Execute FFmpeg transcoding with H.265 and fMP4 (.m4s) segments."""
    logger.info("\n" + "-"*60)
    logger.info(f"TRANSCODING: {profile_info['name']} Profile (fMP4/CMAF)")
    logger.info(f"{'-'*60}")
    
    playlist_out = os.path.join(OUTPUT_DIR, f"{short_name}.m3u8")
    # Note: We use %03d.m4s pattern for fMP4 segments
    segments_pattern = os.path.join(OUTPUT_DIR, f"{short_name}_%03d.m4s")
    
    logger.info(f"Configuration:")
    logger.info(f"   Scale filter: {scale_filter}")
    logger.info(f"   CRF value: {CRF_VALUE}")
    logger.info(f"   Preset: {PRESET}")
    logger.info(f"   Audio codec: {AUDIO_CODEC}")
    logger.info(f"   Audio bitrate: {AUDIO_BITRATE}")
    logger.info(f"   Segment format: fMP4 (.m4s)")
    logger.info(f"   Output playlist: {playlist_out}")
    
    cmd = [
        "ffmpeg", "-y", "-i", INPUT_VIDEO,
        "-vf", f"scale={scale_filter}",
        "-c:v", "libx265",           # H.265/HEVC codec
        "-crf", str(CRF_VALUE),       # Quality-based encoding
        "-preset", PRESET,            # Encoding speed preset
        "-c:a", AUDIO_CODEC,          # Audio codec
        "-b:a", AUDIO_BITRATE,        # Audio bitrate
        "-g", "60",                   # Keyframe interval
        "-sc_threshold", "0",         # Disable scene change detection
        "-hls_time", "4",             # 4-second segments
        "-hls_playlist_type", "vod",  # VOD mode
        "-hls_segment_type", "fmp4",  # <<< KEY CHANGE: Use fMP4 segments
        "-hls_fmp4_init_filename", f"{short_name}_init.mp4",  # Init segment
        "-hls_segment_filename", segments_pattern,
        "-hls_flags", "independent_segments+program_date_time",
        playlist_out
    ]
    
    start_time = datetime.now()
    logger.info(f"⏱️  Starting transcode at: {start_time.strftime('%H:%M:%S')}")
    
    success, output = run_command(cmd, f"H.265 fMP4 transcode for {short_name}")
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    if not success:
        logger.error(f"❌ FFmpeg transcode failed for {short_name}")
        sys.exit(1)
    
    # Count generated segment files (.m4s)
    generated_files = list(Path(OUTPUT_DIR).glob(f"{short_name}_*.m4s"))
    init_file = Path(OUTPUT_DIR) / f"{short_name}_init.mp4"
    
    total_size = sum(f.stat().st_size for f in generated_files)
    if init_file.exists():
        total_size += init_file.stat().st_size
    
    size_mb = total_size / (1024 * 1024)
    
    logger.info(f"✅ Transcoding completed for {short_name}")
    logger.info(f"   Time elapsed: {duration:.2f} seconds ({duration/60:.2f} minutes)")
    logger.info(f"   Generated {len(generated_files)} video segments (.m4s)")
    logger.info(f"   Init segment: {init_file.name} ({'exists' if init_file.exists() else 'MISSING'})")
    logger.info(f"   Total size: {size_mb:.2f} MB")
    
    # Verify playlist was created
    if os.path.exists(playlist_out):
        playlist_size = os.path.getsize(playlist_out)
        logger.info(f"   Playlist file created: {playlist_out} ({playlist_size} bytes)")
    else:
        logger.error(f"❌ Playlist file was not created: {playlist_out}")
        sys.exit(1)

def generate_master_manifest(active_profiles):
    """Assemble generated configuration profiles into a dynamic master.m3u8 file."""
    logger.info("\n" + "="*60)
    logger.info("STEP 5/7: GENERATING MASTER MANIFEST")
    logger.info("="*60)
    
    master_content = "#EXTM3U\n#EXT-X-VERSION:7\n\n"  # Version 7 for fMP4 support
    
    logger.info(f"Adding {len(active_profiles)} quality profiles to master manifest:")
    
    for profile in active_profiles:
        stream_info = f"#EXT-X-STREAM-INF:BANDWIDTH={profile['bandwidth']},RESOLUTION={profile['res']}"
        playlist_ref = f"{profile['name']}.m3u8"
        
        master_content += f"{stream_info}\n{playlist_ref}\n\n"
        
        logger.info(f"   ✅ {profile['name']}: {profile['res']} @ ~{profile['bandwidth']//1000}kbps")
        
    master_file_path = os.path.join(OUTPUT_DIR, "master.m3u8")
    
    try:
        with open(master_file_path, "w") as f:
            f.write(master_content)
        
        file_size = os.path.getsize(master_file_path)
        logger.info(f"✅ Master manifest written successfully")
        logger.info(f"   Location: {master_file_path}")
        logger.info(f"   File size: {file_size} bytes")
        logger.info(f"   Content preview:\n{master_content[:200]}...\n")
        
    except Exception as e:
        logger.error(f"❌ Failed to write master manifest: {e}")
        sys.exit(1)

def upload_to_rustfs():
    """Sync local processed files to RustFS/MinIO storage bucket."""
    logger.info("\n" + "="*60)
    logger.info("STEP 6/7: UPLOADING TO RUSTFS/MINIO BUCKET")
    logger.info("="*60)
    
    destination = f"{MC_ALIAS_PATH}/{VIDEO_ID}/"
    logger.info(f"Source: {OUTPUT_DIR}/")
    logger.info(f"Destination: {destination}")
    
    # List what we're about to upload
    files_to_upload = list(Path(OUTPUT_DIR).rglob("*"))
    total_size = sum(f.stat().st_size for f in files_to_upload if f.is_file())
    size_mb = total_size / (1024 * 1024)
    
    logger.info(f"Files to upload: {len([f for f in files_to_upload if f.is_file()])}")
    logger.info(f"Total upload size: {size_mb:.2f} MB")
    
    # Upload using mc cp with recursive flag
    cmd = ["mc", "cp", "--recursive", f"{OUTPUT_DIR}/", destination]
    
    start_time = datetime.now()
    logger.info(f"⏱️  Starting upload at: {start_time.strftime('%H:%M:%S')}")
    
    success, output = run_command(cmd, f"Uploading to {destination}")
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    if not success:
        logger.error(f"❌ Upload failed after {duration:.2f} seconds")
        sys.exit(1)
    
    logger.info(f"✅ Upload completed successfully")
    logger.info(f"   Time elapsed: {duration:.2f} seconds")
    
    # Verify upload by listing remote contents
    verify_cmd = ["mc", "ls", "--recursive", destination]
    logger.info(f"Verifying uploaded files...")
    success, remote_listing = run_command(verify_cmd, f"Verifying upload at {destination}")
    
    if success and remote_listing:
        remote_files = [line for line in remote_listing.strip().split('\n') if line]
        logger.info(f"✅ Verified {len(remote_files)} files in remote bucket")
        logger.info(f"Remote contents:\n{remote_listing}")
    else:
        logger.warning(f"⚠️  Could not verify remote upload")

def main():
    logger.info("\n" + "#"*60)
    logger.info("# STARTING HLS VIDEO PACKAGING PIPELINE")
    logger.info("#"*60)
    logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Input Video: {INPUT_VIDEO}")
    logger.info(f"Video ID: {VIDEO_ID}")
    logger.info(f"Output Directory: {OUTPUT_DIR}")
    logger.info(f"Bucket Path: {MC_ALIAS_PATH}/{VIDEO_ID}/")
    logger.info(f"Codec: H.265/HEVC (libx265)")
    logger.info(f"Container: fMP4 (.m4s)")
    logger.info(f"CRF: {CRF_VALUE}")
    logger.info(f"Preset: {PRESET}")
    logger.info(f"Audio: {AUDIO_CODEC} @ {AUDIO_BITRATE}")
    logger.info("#"*60 + "\n")
    
    # Step 1: Check dependencies
    check_dependencies()
    
    # Step 2: Get input resolution
    _, native_height = get_input_resolution()
    
    # Step 3: Prepare local workspace
    prepare_workspace()
    
    # Step 4: Clear old bucket contents
    clear_bucket_folder()
    
    # Define encoding profiles with estimated bandwidths for H.265
    # Note: H.265 needs lower bitrates than H.264 for same quality
    all_possible_profiles = [
        {"name": "1080p", "scale": "1920:-2", "bandwidth": 3500000, "res": "1920x1080", "threshold": 1080},
        {"name": "720p",  "scale": "1280:-2", "bandwidth": 1800000, "res": "1280x720",  "threshold": 720},
        # {"name": "480p",  "scale": "854:-2",  "bandwidth": 800000, "res": "854x480",  "threshold": 480}
    ]
    
    # Filter profiles based on source video resolution
    active_profiles = [p for p in all_possible_profiles if native_height >= p["threshold"]]
    
    # Handle edge case: very low resolution source
    if not active_profiles:
        logger.warning(f"⚠️  Input video ({native_height}p) is below minimum threshold")
        logger.info(f"   Creating fallback profile at native resolution")
        active_profiles.append({
            "name": "fallback", 
            "scale": "trunc(iw/2)*2:trunc(ih/2)*2", 
            "bandwidth": 500000, 
            "res": f"{native_height}p", 
            "threshold": 0
        })

    logger.info("\n" + "="*60)
    logger.info("STEP 7/7: INITIATING TRANSCODING PIPELINE")
    logger.info("="*60)
    logger.info(f"Active quality profiles: {[p['name'] for p in active_profiles]}")
    logger.info(f"Total profiles to encode: {len(active_profiles)}\n")
    
    # Encode each profile
    for idx, profile in enumerate(active_profiles, 1):
        logger.info(f"\n>>> Processing profile {idx}/{len(active_profiles)}: {profile['name']}")
        run_ffmpeg_transcode(profile["scale"], profile["name"], profile)
    
    # Generate master manifest
    generate_master_manifest(active_profiles)
    
    # Upload to bucket
    upload_to_rustfs()
    
    # Final summary
    logger.info("\n" + "#"*60)
    logger.info("# ✅ PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("#"*60)
    logger.info(f"Completion time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"\n📺 HLS Stream URL:")
    logger.info(f"   http://192.168.1.188:9000/video-streams2/{VIDEO_ID}/master.m3u8")
    logger.info(f"\n📁 Local output directory: {os.path.abspath(OUTPUT_DIR)}")
    logger.info(f"🗂️  Remote bucket path: {MC_ALIAS_PATH}/{VIDEO_ID}/")
    logger.info("#"*60 + "\n")

if __name__ == "__main__":
    main()
