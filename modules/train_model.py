import pandas as pd
import joblib

from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report


# 1. Load Dataset

DATA_PATH = "data/cleaned_triage_data.csv"

data = pd.read_csv(DATA_PATH)

print("Dataset loaded successfully!")
print("Dataset shape:", data.shape)
print("Columns:", data.columns.tolist())


# 2. Clean Data

# Text columns
data["symptoms"] = data["symptoms"].fillna("").astype(str)
data["severity"] = data["severity"].fillna("Mild").astype(str)

# Numeric columns
data["age"] = pd.to_numeric(data["age"], errors="coerce")
data["duration_days"] = pd.to_numeric(
    data["duration_days"],
    errors="coerce"
)
data["severity_score"] = pd.to_numeric(
    data["severity_score"],
    errors="coerce"
)

# Remove rows with invalid numeric values
data = data.dropna(
    subset=[
        "age",
        "duration_days",
        "severity_score",
        "urgency"
    ]
)

print("Cleaned dataset shape:", data.shape)


# 3. Features and Target

X = data[
    [
        "symptoms",
        "age",
        "severity",
        "duration_days",
        "severity_score"
    ]
]

y = data["urgency"].astype(str)


# 4. Train-Test Split

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.20,
    random_state=42,
    stratify=y
)


# 5. Preprocessing

# Symptoms → TF-IDF
symptoms_pipeline = Pipeline(
    steps=[
        (
            "tfidf",
            TfidfVectorizer(
                max_features=1000,
                ngram_range=(1, 2)
            )
        )
    ]
)


# Severity → One-Hot Encoding
severity_pipeline = Pipeline(
    steps=[
        (
            "onehot",
            OneHotEncoder(
                handle_unknown="ignore"
            )
        )
    ]
)


# Numeric columns → Imputer + Scaling
numeric_pipeline = Pipeline(
    steps=[
        (
            "imputer",
            SimpleImputer(strategy="median")
        ),
        (
            "scaler",
            StandardScaler()
        )
    ]
)


# 6. Column Transformer

preprocessor = ColumnTransformer(
    transformers=[
        (
            "symptoms",
            symptoms_pipeline,
            "symptoms"
        ),
        (
            "severity",
            severity_pipeline,
            ["severity"]
        ),
        (
            "numeric",
            numeric_pipeline,
            [
                "age",
                "duration_days",
                "severity_score"
            ]
        )
    ]
)


# 7. Machine Learning Model

model = Pipeline(
    steps=[
        (
            "preprocessor",
            preprocessor
        ),
        (
            "classifier",
            LogisticRegression(
                max_iter=1000
            )
        )
    ]
)


# 8. Train Model

print("\nTraining model...")

model.fit(
    X_train,
    y_train
)

print("Model trained successfully!")


# 9. Test Model

predictions = model.predict(X_test)

accuracy = accuracy_score(
    y_test,
    predictions
)

print("\nModel Accuracy:")
print(
    round(accuracy * 100, 2),
    "%"
)

print("\nClassification Report:")
print(
    classification_report(
        y_test,
        predictions
    )
)


# 10. Save Model

MODEL_PATH = "models/triage_model.pkl"

joblib.dump(
    model,
    MODEL_PATH
)

print("\nModel saved successfully!")
print("Saved at:", MODEL_PATH)