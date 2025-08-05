import os
import sys
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path

# === LOAD ENV & INITIALIZE OPENAI CLIENT ===
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise EnvironmentError("Please set your OPENAI_API_KEY environment variable.")

client = OpenAI(api_key=api_key)

# === PATHS ===
PROMPT_DIR = Path("/Volumes/Rachna-HD/Dataset/GeneratedPromptsClientsExp3")
OUTPUT_ROOT = Path("/Volumes/Rachna-HD/GeneratedOutputClientsExp3") / "GPT4o"
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

# === LLM CALL ===
def call_gpt_4o(prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error occurred while generating test: {e}"

# === PROCESS PROMPTS ===
# def process_prompts(prompt_dir):
#     print(f"Scanning prompt root: {prompt_dir.resolve()}\n")

#     bump_folders = [p for p in prompt_dir.iterdir() if p.is_dir()]
#     if not bump_folders:
#         print("No BUMP instance folders found.")
#         return

#     for bump_folder in bump_folders:
#         bump_id = bump_folder.name
#         txt_files = list(bump_folder.glob("*.txt"))
#         if not txt_files:
#             print(f"No prompts found in {bump_id}")
#             continue

#         print(f"\nBUMP Instance: {bump_id} — {len(txt_files)} prompt(s)\n")

#         for txt_file in txt_files:
#             prompt_filename = txt_file.name
#             output_path = OUTPUT_ROOT / bump_id / prompt_filename

#             with open(txt_file, "r", encoding="utf-8") as f:
#                 prompt = f.read()

#             print(f"Processing: {txt_file}")
#             response = call_gpt_4o(prompt)

#             if response:
#                 output_path.parent.mkdir(parents=True, exist_ok=True)
#                 with open(output_path, "w", encoding="utf-8") as out_file:
#                     out_file.write(response)
#                 print(f"Saved: {output_path}")
#             else:
#                 print(f"No response for: {txt_file}")


def process_prompts(prompt_dir):
    print(f"Scanning prompt root: {prompt_dir.resolve()}\n")

    bump_folders = [p for p in prompt_dir.iterdir() if p.is_dir()]
    if not bump_folders:
        print("No BUMP instance folders found.")
        return

    for bump_folder in bump_folders:
        bump_id = bump_folder.name
        txt_files = list(bump_folder.glob("*.txt"))
        if not txt_files:
            print(f"No prompts found in {bump_id}")
            continue

        print(f"\nBUMP Instance: {bump_id} — {len(txt_files)} prompt(s)\n")

        for txt_file in txt_files:
            prompt_filename = txt_file.name
            output_path = OUTPUT_ROOT / bump_id / prompt_filename

            # === Skip if output already exists ===
            if output_path.exists():
                print(f"Skipping (already exists): {output_path}")
                continue

            with open(txt_file, "r", encoding="utf-8") as f:
                prompt = f.read()

            print(f"Processing: {txt_file}")
            response = call_gpt_4o(prompt)

            if response:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as out_file:
                    out_file.write(response)
                print(f"Saved: {output_path}")
            else:
                print(f"No response for: {txt_file}")


# === MAIN ===
if __name__ == "__main__":
    process_prompts(PROMPT_DIR)
    print("\All model responses saved under /Volumes/Rachna-HD/GeneratedOutputClientsExp3/GPT4o/")
    print(f"Full log saved to: {LOG_FILE}")
