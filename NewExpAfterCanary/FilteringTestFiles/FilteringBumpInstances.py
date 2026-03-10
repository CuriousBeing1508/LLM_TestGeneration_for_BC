# import os
# import pandas as pd

# # --- Config ---
# CSV_PATH = "/Volumes/Rachna-HD/RQResults/RQ4_resultsBUMP.csv"
# FOLDERS_DIR = "/Volumes/Rachna-HD/FilteredDataset/Exp6Prompts"
# OUTPUT_PATH = "/Volumes/Rachna-HD/ConfigFiles/Candidate_BUMP_Instance_errorTypes.csv"
# # --------------

# df = pd.read_csv(CSV_PATH)

# existing_folders = set(os.listdir(FOLDERS_DIR))

# df["folder_found"] = df["custom_id"].astype(str).isin(existing_folders)

# df.to_csv(OUTPUT_PATH, index=False)

# print(f"Done. {df['folder_found'].sum()} / {len(df)} rows marked as found.")



import os
import pandas as pd

# --- Config ---
CSV_PATH = "/Volumes/Rachna-HD/RQResults/RQ4_resultsBUMP.csv"
FOLDERS_DIR = "/Volumes/Rachna-HD/FilteredDataset/Exp6Prompts"
OUTPUT_PATH = "/Volumes/Rachna-HD/ConfigFiles/Candidate_BUMP_Instance_errorTypes.csv"
# --------------

df = pd.read_csv(CSV_PATH)

existing_folders = set(os.listdir(FOLDERS_DIR))

df = df[df["custom_id"].astype(str).isin(existing_folders)]

df.to_csv(OUTPUT_PATH, index=False)

print(f"Done. {len(df)} rows saved.")