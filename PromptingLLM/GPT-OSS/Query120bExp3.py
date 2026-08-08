# Got into hourly limits, and i also notice they have weekly limits as well. Need to be careful as they do not give numbers.
# Updated with multi-API key rotation support
import os
import sys
import csv
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from ollama import Client
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import SECONDARY_DRIVE
        

# === CONFIGURATION ===
START_IDX = 1    # Start row (1-based index)
END_IDX = 190    # End row (None = all)

# Rate limiting configuration
REQUEST_DELAY = 2.0  # Seconds to wait between requests (adjust as needed)
HOURLY_WAIT_TIME = 3660  # Wait 61 minutes when hitting hourly limit (3600s + 60s buffer)

# Weekly limit handling: Set to True to exit when hitting weekly limit
# Set to False to wait for 7 days (not recommended)
EXIT_ON_WEEKLY_LIMIT = True


CSV_PATH = SECONDARY_DRIVE / "ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv"

# === LOAD ENV ===
load_dotenv()

# === API KEY MANAGEMENT ===
# Add multiple API keys here. The script will rotate to the next key when weekly limit hits.
API_KEYS = [
    os.getenv("OLLAMA_API_KEY"),      # Primary key
    os.getenv("OLLAMA_API_KEY_2"),    # Backup key 1
    os.getenv("OLLAMA_API_KEY_3"),    # Backup key 2
    # Add more keys as needed
]

# Filter out None values
API_KEYS = [key for key in API_KEYS if key]

if not API_KEYS:
    raise EnvironmentError("Please set at least one OLLAMA_API_KEY environment variable.")

# Pick the  Cloud model you want (verify this name is correct)
MODEL_NAME = "gpt-oss:120b-cloud"  # Or whatever the actual model name is on Ollama Cloud

# === PATHS ===
PROMPT_DIR = SECONDARY_DRIVE / "FilteredDataset/Exp3Prompts"
OUTPUT_ROOT = SECONDARY_DRIVE / "FilteredDataset/Exp3LLMOutput" / "GPT_OSS_120b"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_ROOT / "generation_log.txt"
PROGRESS_FILE = OUTPUT_ROOT / "progress.json"
RATE_LIMIT_FILE = OUTPUT_ROOT / "rate_limit_info.json"
CURRENT_KEY_INDEX_FILE = OUTPUT_ROOT / "current_key_index.json"

# === SETUP LOGGING ===
class Logger:
    def __init__(self, logfile_path):
        self.terminal = sys.stdout
        self.log = open(logfile_path, "a", encoding="utf-8")  # Changed to append mode

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger(LOG_FILE)


# === KEY ROTATION FUNCTIONS ===
def get_current_key_index():
    """Load the current API key index."""
    if CURRENT_KEY_INDEX_FILE.exists():
        with open(CURRENT_KEY_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("index", 0)
    return 0


def save_current_key_index(index):
    """Save the current API key index."""
    with open(CURRENT_KEY_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "index": index, 
            "timestamp": datetime.now().isoformat(),
            "total_keys": len(API_KEYS)
        }, f, indent=2)


def get_next_api_key():
    """Get the next available API key. Returns (new_client, new_index, has_more_keys)."""
    current_index = get_current_key_index()
    next_index = current_index + 1
    
    if next_index >= len(API_KEYS):
        return None, current_index, False  # No more keys available
    
    # Create new client with next key
    new_client = Client(
        host="https://ollama.com",
        headers={"Authorization": f"Bearer {API_KEYS[next_index]}"}
    )
    
    save_current_key_index(next_index)
    return new_client, next_index, True


# === INIT OLLAMA CLOUD CLIENT ===
current_key_index = get_current_key_index()
client = Client(
    host="https://ollama.com",
    headers={"Authorization": f"Bearer {API_KEYS[current_key_index]}"}
)

print(f"\n{'='*80}")
print(f"API Key Configuration:")
print(f"  Total API keys available: {len(API_KEYS)}")
print(f"  Currently using API key: {current_key_index + 1} of {len(API_KEYS)}")
print(f"  Keys remaining: {len(API_KEYS) - current_key_index - 1}")
print(f"{'='*80}\n")


# === EMAIL NOTIFICATION ===
def send_email_notification(subject, message):
    """Send email notification if configured."""
    if not NOTIFICATION_EMAIL or not EMAIL_PASSWORD:
        return
    
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart()
        msg['From'] = NOTIFICATION_EMAIL
        msg['To'] = NOTIFICATION_EMAIL
        msg['Subject'] = subject
        
        msg.attach(MIMEText(message, 'plain'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(NOTIFICATION_EMAIL, EMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(NOTIFICATION_EMAIL, NOTIFICATION_EMAIL, text)
        server.quit()
        
        print(f"✉ Email notification sent to {NOTIFICATION_EMAIL}")
    except Exception as e:
        print(f"Failed to send email notification: {e}")


# === RATE LIMIT TRACKING ===
def save_rate_limit_info(limit_type, hit_time, estimated_reset_time):
    """Save rate limit information for future reference."""
    rate_limit_data = {
        "limit_type": limit_type,
        "hit_time": hit_time.isoformat(),
        "estimated_reset_time": estimated_reset_time.isoformat(),
        "current_key_index": get_current_key_index(),
        "total_keys": len(API_KEYS),
        "message": f"{limit_type} limit hit. Resume after {estimated_reset_time.strftime('%Y-%m-%d %H:%M:%S')}"
    }
    with open(RATE_LIMIT_FILE, "w", encoding="utf-8") as f:
        json.dump(rate_limit_data, f, indent=2)


def load_rate_limit_info():
    """Load rate limit information if exists."""
    if RATE_LIMIT_FILE.exists():
        with open(RATE_LIMIT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# === PROGRESS TRACKING ===
def save_progress(bump_id, txt_file_name):
    """Save the current progress to resume from in case of interruption."""
    progress_data = {
        "last_bump_id": bump_id,
        "last_file": txt_file_name,
        "timestamp": datetime.now().isoformat(),
        "current_key_index": get_current_key_index()
    }
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=2)


def load_progress():
    """Load the last saved progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# === OLLAMA CLOUD CALL WITH RATE LIMIT HANDLING AND KEY ROTATION ===
def call_cloud_model(prompt: str, max_retries=3):
    """
    Calls model via Ollama Cloud with rate limit handling and automatic key rotation.
    Returns tuple: (response_text, should_exit)
    
    Args:
        prompt: The prompt to send
        max_retries: Maximum number of retries for transient errors
        
        We controll for randomness (temp=0), but let each model use its default configuration to show real-world performance
    """
    global client  # We need to modify the global client when switching keys
    
    for attempt in range(max_retries):
        try:
            response = client.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                options={
    "temperature": 0,       # Deterministic generation
}
            )
            return response["message"]["content"], False

        except Exception as e:
            error_msg = str(e).lower()
            
            # Check for WEEKLY rate limit error
            if "weekly" in error_msg and ("limit" in error_msg or "429" in str(e)):
                current_time = datetime.now()
                current_index = get_current_key_index()
                
                print(f"\n{'='*80}")
                print(f" WEEKLY RATE LIMIT HIT - API KEY {current_index + 1} of {len(API_KEYS)}")
                print(f"Error: {e}")
                print(f"Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*80}\n")
                
                # Try to switch to next API key
                new_client, new_index, has_more_keys = get_next_api_key()
                
                if has_more_keys:
                    print(f"✓ Switching to backup API key {new_index + 1} of {len(API_KEYS)}")
                    print(f"✓ Keys remaining after this: {len(API_KEYS) - new_index - 1}")
                    print(f"✓ Continuing processing with new key...\n")
                    
                    client = new_client  # Update global client
                    
                    # Send notification about key switch
                    email_subject = "Ollama Cloud - Switched to Backup API Key"
                    email_body = f"""Weekly rate limit hit at: {current_time.strftime('%Y-%m-%d %H:%M:%S')}

Switched from API key {current_index + 1} to API key {new_index + 1}.

The script is continuing automatically with the new key.

Keys remaining: {len(API_KEYS) - new_index - 1}

Check the log file for more details:
{LOG_FILE}
"""
                    send_email_notification(email_subject, email_body)
                    
                    # Retry immediately with new key
                    continue
                
                else:
                    # No more keys available
                    print(f"❌ All {len(API_KEYS)} API keys have hit their weekly limits!")
                    
                    if EXIT_ON_WEEKLY_LIMIT:
                        estimated_reset = current_time + timedelta(days=7)
                        save_rate_limit_info("WEEKLY_ALL_KEYS", current_time, estimated_reset)
                        
                        print(f"\n⚠️ All keys exhausted. Exiting gracefully.")
                        print(f"   Progress has been saved.")
                        print(f"   Estimated reset time: {estimated_reset.strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"   (Note: Actual reset time may be different)")
                        print(f"\n📋 To resume:")
                        print(f"   1. Wait for the weekly limits to reset")
                        print(f"   2. Delete {CURRENT_KEY_INDEX_FILE.name} to reset to first key")
                        print(f"   3. Run this script again - it will resume from where it stopped")
                        print(f"{'='*80}\n")
                        
                        email_subject = "Ollama Cloud - ALL API Keys Weekly Limit Hit"
                        email_body = f"""All {len(API_KEYS)} API keys hit weekly limit at: {current_time.strftime('%Y-%m-%d %H:%M:%S')}

Estimated reset time: {estimated_reset.strftime('%Y-%m-%d %H:%M:%S')}

The script has exited gracefully. Progress has been saved.

To resume:
1. Wait for the weekly limits to reset
2. Delete {CURRENT_KEY_INDEX_FILE} to reset to first key
3. Run the script again

Check the log file for more details:
{LOG_FILE}
"""
                        send_email_notification(email_subject, email_body)
                        
                        return None, True  # Signal to exit
                    else:
                        # Wait for 7 days
                        print(f"⏳ Waiting for 7 days before resuming...")
                        estimated_reset = current_time + timedelta(days=7)
                        save_rate_limit_info("WEEKLY_ALL_KEYS", current_time, estimated_reset)
                        
                        # Reset to first key after waiting
                        save_current_key_index(0)
                        client = Client(
                            host="https://ollama.com",
                            headers={"Authorization": f"Bearer {API_KEYS[0]}"}
                        )
                        
                        print(f"Will resume at: {estimated_reset.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        time.sleep(7 * 24 * 60 * 60)
                        
                        print(f"\n{'='*80}")
                        print(f"Resuming after weekly rate limit wait...")
                        print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                        print(f"{'='*80}\n")
                        
                        continue
            
            # Check for HOURLY rate limit error (429)
            elif "429" in str(e) or "hourly" in error_msg:
                current_time = datetime.now()
                current_index = get_current_key_index()
                
                print(f"\n{'='*80}")
                print(f"⏱️ HOURLY RATE LIMIT HIT - API KEY {current_index + 1}")
                print(f"Error: {e}")
                print(f"Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"Waiting for {HOURLY_WAIT_TIME // 60} minutes before resuming...")
                print(f"{'='*80}\n")
                
                wait_until = current_time + timedelta(seconds=HOURLY_WAIT_TIME)
                save_rate_limit_info("HOURLY", current_time, wait_until)
                
                print(f"Will resume at: {wait_until.strftime('%Y-%m-%d %H:%M:%S')}\n")
                
                time.sleep(HOURLY_WAIT_TIME)
                
                print(f"\n{'='*80}")
                print(f"Resuming after hourly rate limit wait...")
                print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*80}\n")
                
                continue
            
            # Check for other retryable errors
            elif "503" in str(e) or "timeout" in error_msg or "connection" in error_msg:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 5  # Exponential backoff: 5s, 10s, 20s
                    print(f"Transient error occurred: {e}")
                    print(f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    return f"Error occurred after {max_retries} retries: {e}", False
            
            # For other errors, return immediately
            else:
                return f"Error occurred while generating test: {e}", False
    
    return "Error: Max retries exceeded", False


# === CSV LOADER ===
def load_bumps_from_csv(csv_path: Path):
    bump_ids = []
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found at {csv_path}")

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = (row.get("custom_id") or "").strip()
            if cid:
                bump_ids.append(cid)

    return bump_ids


# === PROCESS PROMPTS WITH RESUME CAPABILITY ===
def process_prompts(prompt_dir, csv_bump_ids, start_idx=1, end_idx=None, skip_existing=True):
    # adjust 1-based → 0-based
    start_idx = max(0, start_idx - 1)
    end_slice = end_idx if end_idx is None else end_idx

    selected_ids = csv_bump_ids[start_idx:end_slice]

    print(f"\n{'='*80}")
    print(f"Starting batch processing at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    print(f"Total bump instances in CSV: {len(csv_bump_ids)}")
    print(f"Configured range: {start_idx + 1} to {start_idx + len(selected_ids)}")
    print(f"Request delay between calls: {REQUEST_DELAY} seconds")
    print(f"Hourly limit wait time: {HOURLY_WAIT_TIME // 60} minutes")
    print(f"Weekly limit action: {'EXIT (recommended)' if EXIT_ON_WEEKLY_LIMIT else 'WAIT 7 days'}")
    print("Instances to process:", selected_ids, "\n")

    # Check for previous rate limit info
    rate_limit_info = load_rate_limit_info()
    if rate_limit_info:
        print(f" Previous rate limit detected:")
        print(f"   Type: {rate_limit_info['limit_type']}")
        print(f"   Hit at: {rate_limit_info['hit_time']}")
        print(f"   Estimated reset: {rate_limit_info['estimated_reset_time']}")
        print(f"   Message: {rate_limit_info['message']}")
        print(f"   Continuing with current run...\n")

    bump_folders = [prompt_dir / bid for bid in selected_ids if (prompt_dir / bid).exists()]

    total_files = sum(len(list(f.glob("*.txt"))) for f in bump_folders)
    processed = 0
    written = 0
    skipped = 0
    errors = 0

    # Check if we need to resume from a previous run
    progress = load_progress()
    should_skip = progress is not None
    
    if progress:
        print(f"\n{'='*80}")
        print(f"RESUMING FROM PREVIOUS RUN")
        print(f"Last processed: {progress['last_bump_id']} / {progress['last_file']}")
        print(f"Previous run timestamp: {progress['timestamp']}")
        if 'current_key_index' in progress:
            print(f"Was using API key: {progress['current_key_index'] + 1}")
        print(f"{'='*80}\n")

    for bump_folder in bump_folders:
        bump_id = bump_folder.name
        txt_files = sorted(list(bump_folder.glob("*.txt")))

        if not txt_files:
            print(f"No prompts found in {bump_id}")
            continue

        print(f"\n{'-'*80}")
        print(f"BUMP Instance: {bump_id} — {len(txt_files)} prompt(s)")
        print(f"{'-'*80}\n")

        for txt_file in txt_files:
            # Skip until we reach the last processed file (if resuming)
            if should_skip:
                if bump_id == progress['last_bump_id'] and txt_file.name == progress['last_file']:
                    should_skip = False
                    print(f"Found resume point: {bump_id}/{txt_file.name}")
                    print(f"Continuing from next file...\n")
                continue

            processed += 1
            timestamp = datetime.now().strftime('%H:%M:%S')
            current_key = get_current_key_index()
            print(f"[{timestamp}] [{processed}/{total_files}] [Key {current_key + 1}/{len(API_KEYS)}] Processing {txt_file}")

            prompt_filename = txt_file.name
            output_path = OUTPUT_ROOT / bump_id / prompt_filename

            if skip_existing and output_path.exists():
                print(f" Skipping (already exists): {output_path}")
                skipped += 1
                continue

            with open(txt_file, "r", encoding="utf-8") as f:
                prompt = f.read()

            # Call the API with rate limit handling
            response, should_exit = call_cloud_model(prompt)
            
            # Check if we need to exit due to weekly limit
            if should_exit:
                print(f"\n{'='*80}")
                print(f"BATCH PROCESSING INTERRUPTED - WEEKLY LIMIT (ALL KEYS)")
                print(f"Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*80}")
                print(f"Progress at time of interruption:")
                print(f"  Prompt files processed: {processed}/{total_files}")
                print(f"  New outputs written: {written}")
                print(f"  Already existing/skipped: {skipped}")
                print(f"  Errors encountered: {errors}")
                print(f"  Last file: {bump_id}/{txt_file.name}")
                print(f"{'='*80}\n")
                return  # Exit the function

            if response and not response.startswith("Error"):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as out_file:
                    out_file.write(response)

                print(f"Saved: {output_path}")
                written += 1
                
                # Save progress after each successful write
                save_progress(bump_id, txt_file.name)
            else:
                print(f"Failed: {response}")
                errors += 1

            # Add delay between requests to avoid hitting rate limits
            if processed < total_files:  # Don't wait after the last file
                time.sleep(REQUEST_DELAY)

    print(f"\n{'='*80}")
    print(f"BATCH PROCESSING COMPLETE")
    print(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    print(f"Selected bump instances: {len(selected_ids)}")
    print(f"Prompt files found: {total_files}")
    print(f"New outputs written: {written}")
    print(f"Already existing/skipped: {skipped}")
    print(f"Errors encountered: {errors}")
    print(f"Final API key used: {get_current_key_index() + 1} of {len(API_KEYS)}")
    print(f"All model responses saved under {OUTPUT_ROOT}")
    print(f"Full log saved to: {LOG_FILE}")
    print(f"Progress tracking file: {PROGRESS_FILE}")
    print(f"{'='*80}\n")


# === MAIN ===
if __name__ == "__main__":
    try:
        csv_bump_ids = load_bumps_from_csv(CSV_PATH)
        process_prompts(PROMPT_DIR, csv_bump_ids, start_idx=START_IDX, end_idx=END_IDX)
    except KeyboardInterrupt:
        print(f"\n\n{'='*80}")
        print("Script interrupted by user")
        print("Progress has been saved. Run the script again to resume.")
        print(f"Current API key: {get_current_key_index() + 1} of {len(API_KEYS)}")
        print(f"{'='*80}\n")
    except Exception as e:
        print(f"\n\n{'='*80}")
        print(f"Fatal error: {e}")
        print(f"{'='*80}\n")
        raise