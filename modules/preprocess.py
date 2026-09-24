import pandas as pd


def load_data():
    data = pd.read_csv("data/triage_data.csv")
    return data


def clean_data(data):
    data = data.dropna()
    data["symptoms"] = data["symptoms"].str.lower().str.strip()
    data["severity"] = data["severity"].str.strip()
    data["urgency"] = data["urgency"].str.strip()

    return data


if __name__ == "__main__":
    data = load_data()
    data = clean_data(data)

    print("Dataset loaded successfully!")
    print("\nDataset shape:", data.shape)
    print("\nColumns:")
    print(data.columns.tolist())
    print("\nUrgency distribution:")
    print(data["urgency"].value_counts())

# Convert age to numeric
data["age"] = pd.to_numeric(data["age"], errors="coerce")

# Convert duration into number of days
data["duration_days"] = (
    data["duration"]
    .astype(str)
    .str.extract(r"(\d+)")
    .astype(float)
)

# Convert severity into numerical score
severity_mapping = {
    "mild": 1,
    "moderate": 2,
    "severe": 3
}

data["severity_score"] = (
    data["severity"]
    .astype(str)
    .str.lower()
    .str.strip()
    .map(severity_mapping)
)

# Remove duplicate rows
data = data.drop_duplicates()

# Check missing values
print("\nMissing values:")
print(data.isnull().sum())

# Save cleaned dataset
data.to_csv("data/cleaned_triage_data.csv", index=False)

print("\nCleaned dataset saved successfully!")