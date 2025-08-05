import pandas as pd
# Just trying to save the docker image file name for an ease to execute docker command easily..
# Load the original file
df = pd.read_csv("/Volumes/Rachna-HD/FinalBUMP_Instances.csv")

# Strip and check valid breakingCommit
df['breakingCommit'] = df['breakingCommit'].astype(str).str.strip()

# Only add docker image names where breakingCommit is valid
df['docker_image_pre'] = df['breakingCommit'].apply(
    lambda commit: f"ghcr.io/chains-project/breaking-updates:{commit}-pre" if commit else ""
)

df['docker_image_breaking'] = df['breakingCommit'].apply(
    lambda commit: f"ghcr.io/chains-project/breaking-updates:{commit}-breaking" if commit else ""
)

# Save updated CSV
output_path = "/Volumes/Rachna-HD/updated_FinalBUMP_Instances.csv"
df.to_csv(output_path, index=False)

print(f"Updated file saved as: {output_path}")
