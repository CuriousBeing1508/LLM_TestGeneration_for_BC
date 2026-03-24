# Updated for Claude Sonnet 4.6 via Anthropic API
# Rate limit handling with resume support
# Parameter consistency: temperature=0 (deterministic), repetition penalty at default
import os
import sys
import csv
import time
import json
import anthropic
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pathlib import Path

# === CONFIGURATION ===
START_IDX = 1    # Start row (1-based index)
END_IDX = 1   # End row (None = all)

# Rate limiting configuration
REQUEST_DELAY = 2.0  # Seconds to wait between requests (adjust as needed)

CSV_PATH = Path("/Volumes/RachnaPSSD/ConfigFiles/updated_FinalBUMP_Instances_with_TestRunner.csv")

# === LOAD ENV ===
load_dotenv()

# === API KEY ===
API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not API_KEY:
    raise EnvironmentError("Please set ANTHROPIC_API_KEY environment variable.")

# Claude model
MODEL_NAME = "claude-sonnet-4-6"

# Max tokens for response (required by Anthropic API)
# TODO: Set this to match the max_tokens used for Qwen and GPT-4o for consistency
MAX_OUTPUT_TOKENS = 32768  # just setting double higger than the default gpt40 truncation limit. 

# === PATHS ===
PROMPT_DIR = Path("/Volumes/RachnaPSSD/FilteredDataset/Exp3Prompts")
OUTPUT_ROOT = Path("/Volumes/RachnaPSSD/FilteredDataset/Exp3LLMOutput") / "Claude_Sonnet"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_ROOT / "generation_log.txt"
PROGRESS_FILE = OUTPUT_ROOT / "progress.json"

# === SETUP LOGGING ===
class Logger:
    def __init__(self, logfile_path):
        self.terminal = sys.stdout
        self.log = open(logfile_path, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger(LOG_FILE)


# === INIT ANTHROPIC CLIENT ===
client = anthropic.Anthropic(api_key=API_KEY)

print(f"\n{'='*80}")
print(f"Anthropic API Configuration:")
print(f"  Model: {MODEL_NAME}")
print(f"  Max output tokens: {MAX_OUTPUT_TOKENS}")
print(f"  Temperature: 0 (deterministic)")
print(f"{'='*80}\n")


# === PROGRESS TRACKING ===
def save_progress(bump_id, txt_file_name):
    """Save the current progress to resume from in case of interruption."""
    progress_data = {
        "last_bump_id": bump_id,
        "last_file": txt_file_name,
        "timestamp": datetime.now().isoformat()
    }
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=2)


def load_progress():
    """Load the last saved progress."""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# === ANTHROPIC API CALL WITH RATE LIMIT HANDLING ===
def call_cloud_model(prompt: str, max_retries=3):
    """
    Calls Claude Sonnet 4.6 via Anthropic API with rate limit handling.
    Returns tuple: (response_text, should_exit)
    
    We set temperature=0 for deterministic generation.
    Anthropic API does not expose top_p, top_k, or repetition_penalty,
    so those are left at model defaults to keep consistent with paper methodology.
    """
    for attempt in range(max_retries):
        try: # Use streaming to handle long-running requests (required by Anthropic
            # for operations that may take longer than 10 minutes)
            response_text = ""
            with client.messages.stream(
                model=MODEL_NAME,
                max_tokens=MAX_OUTPUT_TOKENS,
                temperature=0,       # Deterministic generation
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    response_text += text

            return response_text, False

        except anthropic.RateLimitError as e:
            # Per-minute rate limit (429) — wait and retry
            current_time = datetime.now()

            # Try to get retry-after from headers
            retry_after = None
            if hasattr(e, 'response') and e.response is not None:
                retry_after = e.response.headers.get('retry-after')

            wait_time = int(retry_after) + 5 if retry_after else 65  # Default ~1 min + buffer

            print(f"\n{'='*80}")
            print(f" RATE LIMIT HIT (429)")
            print(f"Error: {e}")
            print(f"Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Waiting for {wait_time} seconds before retrying...")
            print(f"{'='*80}\n")

            time.sleep(wait_time)
            continue

        except anthropic.APIStatusError as e:
            # Insufficient credits / billing issue (402)
            if e.status_code == 402:
                current_time = datetime.now()

                print(f"\n{'='*80}")
                print(f" INSUFFICIENT CREDITS (402)")
                print(f"Error: {e}")
                print(f"Current time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*80}")
                print(f"\n Your Anthropic account has run out of credits.")
                print(f"   Progress has been saved.")
                print(f"\n To resume:")
                print(f"   1. Add credits at https://console.anthropic.com/settings/billing")
                print(f"   2. Run this script again - it will resume from where it stopped")
                print(f"{'='*80}\n")

                return None, True  # Signal to exit

            # Authentication error (401)
            elif e.status_code == 401:
                print(f"\n{'='*80}")
                print(f"🔑 AUTHENTICATION ERROR (401)")
                print(f"Error: {e}")
                print(f"Check your ANTHROPIC_API_KEY is correct.")
                print(f"{'='*80}\n")
                return None, True  # Signal to exit

            # Server errors (500, 502, 503, 529 overloaded) — retry with backoff
            elif e.status_code in (500, 502, 503, 529):
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) * 5
                    print(f"Transient error occurred (status {e.status_code}): {e}")
                    print(f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                else:
                    return f"Error occurred after {max_retries} retries: {e}", False

            # Other API errors
            else:
                return f"Error occurred while generating test: {e}", False

        except anthropic.APIConnectionError as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 5
                print(f"Connection error: {e}")
                print(f"Retrying in {wait_time} seconds... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
                continue
            else:
                return f"Error occurred after {max_retries} retries: {e}", False

        except Exception as e:
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
    start_idx = max(0, start_idx - 1)
    end_slice = end_idx if end_idx is None else end_idx

    selected_ids = csv_bump_ids[start_idx:end_slice]

    print(f"\n{'='*80}")
    print(f"Starting batch processing at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")
    print(f"Total bump instances in CSV: {len(csv_bump_ids)}")
    print(f"Configured range: {start_idx + 1} to {start_idx + len(selected_ids)}")
    print(f"Request delay between calls: {REQUEST_DELAY} seconds")
    print("Instances to process:", selected_ids, "\n")

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
                    print(f"✓ Found resume point: {bump_id}/{txt_file.name}")
                    print(f"✓ Continuing from next file...\n")
                continue

            processed += 1
            timestamp = datetime.now().strftime('%H:%M:%S')
            print(f"[{timestamp}] [{processed}/{total_files}] Processing {txt_file}")

            prompt_filename = txt_file.name
            output_path = OUTPUT_ROOT / bump_id / prompt_filename

            if skip_existing and output_path.exists():
                print(f"  → Skipping (already exists): {output_path}")
                skipped += 1
                continue

            with open(txt_file, "r", encoding="utf-8") as f:
                prompt = f.read()

            # Call the API with rate limit handling
            response, should_exit = call_cloud_model(prompt)

            # Check if we need to exit (credits exhausted or auth error)
            if should_exit:
                print(f"\n{'='*80}")
                print(f"BATCH PROCESSING INTERRUPTED")
                print(f"Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*80}")
                print(f"Progress at time of interruption:")
                print(f"  Prompt files processed: {processed}/{total_files}")
                print(f"  New outputs written: {written}")
                print(f"  Already existing/skipped: {skipped}")
                print(f"  Errors encountered: {errors}")
                print(f"  Last file: {bump_id}/{txt_file.name}")
                print(f"{'='*80}\n")
                return

            if response and not response.startswith("Error"):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as out_file:
                    out_file.write(response)

                print(f" Saved: {output_path}")
                written += 1

                # Save progress after each successful write
                save_progress(bump_id, txt_file.name)
            else:
                print(f" Failed: {response}")
                errors += 1

            # Add delay between requests to avoid hitting rate limits
            if processed < total_files:
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
        print(f"{'='*80}\n")
    except Exception as e:
        print(f"\n\n{'='*80}")
        print(f"Fatal error: {e}")
        print(f"{'='*80}\n")
        raise