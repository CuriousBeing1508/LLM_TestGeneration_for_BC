import os
import sys
import csv
from dotenv import load_dotenv
from ollama import Client
from pathlib import Path

# === CONFIGURATION ===
START_IDX = 3    # Start row (1-based index)
END_IDX = 190 # End row (None = all)

CSV_PATH = Path("/Volumes/RachnaPSSD/updated_FinalBUMP_Instances_with_TestRunner.csv")

# === LOAD ENV ===
load_dotenv()
api_key = os.getenv("OLLAMA_API_KEY")
if not api_key:
    raise EnvironmentError("Please set your OLLAMA_API_KEY environment variable.")

# === INIT OLLAMA CLOUD CLIENT ===
client = Client(
    host="https://ollama.com",
    headers={"Authorization": f"Bearer {api_key}"}
)

# Pick the Qwen Cloud model you want (verify this name is correct)
MODEL_NAME = "qwen3-coder:480b"  # Or whatever the actual model name is on Ollama Cloud

# === PATHS ===
PROMPT_DIR = Path("/Volumes/RachnaPSSD/GeneratedPromptsClientsExp3")
OUTPUT_ROOT = Path("/Volumes/RachnaPSSD/GeneratedOutputClientsExp3") / "Qwen3_480b_cloud"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE = OUTPUT_ROOT / "generation_log.txt"

# === SETUP LOGGING ===
class Logger:
    def __init__(self, logfile_path):
        self.terminal = sys.stdout
        self.log = open(logfile_path, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        self.terminal.flush()
        self.log.flush()

sys.stdout = Logger(LOG_FILE)


# === OLLAMA CLOUD CALL ===
def call_qwen_cloud(prompt: str):
    """
    Calls Qwen via Ollama Cloud.
    Returns the full text response, matching GPT-4o behavior.
    """
    try:
        response = client.chat(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            stream=False
        )
        return response["message"]["content"]

    except Exception as e:
        return f"Error occurred while generating test: {e}"


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


# === PROCESS PROMPTS ===
def process_prompts(prompt_dir, csv_bump_ids, start_idx=1, end_idx=None, skip_existing=True):
    # adjust 1-based → 0-based
    start_idx = max(0, start_idx - 1)
    end_slice = end_idx if end_idx is None else end_idx

    selected_ids = csv_bump_ids[start_idx:end_slice]

    print(f"Total bump instances in CSV: {len(csv_bump_ids)}")
    print(f"Configured range: {start_idx + 1} to {start_idx + len(selected_ids)}")
    print("Instances to process:", selected_ids, "\n")

    bump_folders = [prompt_dir / bid for bid in selected_ids if (prompt_dir / bid).exists()]

    total_files = sum(len(list(f.glob("*.txt"))) for f in bump_folders)
    processed = 0
    written = 0

    for bump_folder in bump_folders:
        bump_id = bump_folder.name
        txt_files = list(bump_folder.glob("*.txt"))

        if not txt_files:
            print(f"No prompts found in {bump_id}")
            continue

        print(f"\nBUMP Instance: {bump_id} — {len(txt_files)} prompt(s)\n")

        for txt_file in txt_files:
            processed += 1
            print(f"[{processed}/{total_files}] Processing {txt_file}")

            prompt_filename = txt_file.name
            output_path = OUTPUT_ROOT / bump_id / prompt_filename

            if skip_existing and output_path.exists():
                print(f"Skipping (already exists): {output_path}")
                continue

            with open(txt_file, "r", encoding="utf-8") as f:
                prompt = f.read()

            response = call_qwen_cloud(prompt)

            if response:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as out_file:
                    out_file.write(response)

                print(f"Saved: {output_path}")
                written += 1
            else:
                print(f"No response for: {txt_file}")

    print("\n=== SUMMARY ===")
    print(f"Selected bump instances: {len(selected_ids)}")
    print(f"Prompt files found: {total_files}")
    print(f"New outputs written: {written}")
    print(f"Already existing/skipped: {total_files - written}")
    print(f"All model responses saved under {OUTPUT_ROOT}")
    print(f"Full log saved to: {LOG_FILE}")


# === MAIN ===
if __name__ == "__main__":
    csv_bump_ids = load_bumps_from_csv(CSV_PATH)
    process_prompts(PROMPT_DIR, csv_bump_ids, start_idx=START_IDX, end_idx=END_IDX)