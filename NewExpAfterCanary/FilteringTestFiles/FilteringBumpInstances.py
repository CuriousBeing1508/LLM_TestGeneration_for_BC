# import os
# import pandas as pd

# # --- Config ---
# CSV_PATH = PRIMARY_DRIVE / "RQResults/RQ4_resultsBUMP.csv"
# FOLDERS_DIR = PRIMARY_DRIVE / "FilteredDataset/Exp6Prompts"
# OUTPUT_PATH = PRIMARY_DRIVE / "ConfigFiles/Candidate_BUMP_Instance_errorTypes.csv"
# # --------------

# df = pd.read_csv(CSV_PATH)

# existing_folders = set(os.listdir(FOLDERS_DIR))

# df["folder_found"] = df["custom_id"].astype(str).isin(existing_folders)

# df.to_csv(OUTPUT_PATH, index=False)

# print(f"Done. {df['folder_found'].sum()} / {len(df)} rows marked as found.")



import os
import pandas as pd

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from config import PRIMARY_DRIVE

# --- Config ---
CSV_PATH = PRIMARY_DRIVE / "RQResults/RQ4_resultsBUMP.csv"
FOLDERS_DIR = PRIMARY_DRIVE / "FilteredDataset/Exp6Prompts"
OUTPUT_PATH = PRIMARY_DRIVE / "ConfigFiles/Candidate_BUMP_Instance_errorTypes.csv"
# --------------

df = pd.read_csv(CSV_PATH)

existing_folders = set(os.listdir(FOLDERS_DIR))

df = df[df["custom_id"].astype(str).isin(existing_folders)]

df.to_csv(OUTPUT_PATH, index=False)

print(f"Done. {len(df)} rows saved.")