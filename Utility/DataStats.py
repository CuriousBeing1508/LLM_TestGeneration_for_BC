import pandas as pd

# Load CSV
df = pd.read_csv('Library_with_GitHub.csv')

# Step 1: Remove rows where githubURL is NaN, empty, or "No GitHub Repo Found"
df_clean = df.dropna(subset=['githubURL'])
df_clean = df_clean[~df_clean['githubURL'].str.strip().isin(['', 'No GitHub Repo Found'])]

# Step 2: Extract unique clientProjects with their URLs
unique_clients = df_clean[['clientProject', 'url']].drop_duplicates(subset=['clientProject'])
unique_clients.to_csv('unique_client_projects.csv', index=False)

# Step 3: Extract unique dependencyArtifactID with their GitHub URLs
unique_dependencies = df_clean[['dependencyArtifactID', 'githubURL']].drop_duplicates(subset=['dependencyArtifactID'])
unique_dependencies.to_csv('unique_dependencies.csv', index=False)

# Step 4: Compute stats - number of unique clientProjects per dependencyArtifactID
stats = df_clean.groupby('dependencyArtifactID')['clientProject'].nunique().reset_index()
stats.rename(columns={'clientProject': 'uniqueClientProjectsCount'}, inplace=True)
stats.to_csv('dependency_client_stats.csv', index=False)

print("Extraction complete. CSV files generated:")
print("- unique_client_projects.csv")
print("- unique_dependencies.csv")
print("- dependency_client_stats.csv")
