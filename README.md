# MediScan AI

[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/)
[![Live app](https://img.shields.io/badge/live%20app-mediscan--ai--cdu.streamlit.app-FF4B4B.svg)](https://mediscan-ai-cdu.streamlit.app/)

### 🚀 [Live demo](https://mediscan-ai-cdu.streamlit.app/)

Deployed on [Streamlit Community Cloud](https://share.streamlit.io).

An AI-assisted healthcare companion built with Streamlit. MediScan AI combines a
scikit-learn symptom-triage classifier, OCR-based medicine identification backed by
live drug databases, and a Groq-powered conversational assistant — behind a local
authentication gate, with per-user history stored in SQLite.

> ⚠️ **Educational project, not a medical device.** Nothing here is a diagnosis or
> professional medical advice. Always consult a qualified healthcare provider.

---

## Features

| | Feature | What it does |
|---|---|---|
| 🏠 | **Home** | Dashboard with quick actions and service overview |
| 🔍 | **Search** | Global search across the app |
| 🩺 | **Triage** | Symptom → urgency prediction with an emergency keyword override |
| 💊 | **Medicine Scanner** | OCR a strip/pack to identify a medicine, plus manual name search |
| 🤖 | **AI Assistant** | Conversational health Q&A (Groq) in English, हिन्दी, or తెలుగు |
| 🏥 | **Hospital Finder** | Locate nearby facilities and compare healthcare prices |
| ⏰ | **Reminders** | Scheduled medicine reminders with in-app alert and sound |
| 📋 | **History** | Review past triage results and assistant conversations |
| 📁 | **Documents** | Upload and manage personal health documents |
| 👤 | **Profile** | Account details, language, and emergency contact |
| 🔔 | **Notifications** | Notification preferences and alerts |
| 🔐 | **Privacy & Security** | Change password, review sessions, wipe stored data |
| ♿ | **Accessibility** | Larger-text and high-contrast display modes |

**Languages:** the interface ships in 12 languages — English, Hindi, Telugu, Bengali,
Marathi, Tamil, Gujarati, Urdu, Kannada, Odia, Malayalam, and Punjabi. The AI assistant
responds in English, Hindi, or Telugu.

---

## Tech stack

| Layer | Technology |
|---|---|
| UI | Streamlit `>=1.37` |
| Language | Python 3.13 |
| ML | scikit-learn, pandas, numpy, joblib |
| OCR | pytesseract + Pillow (requires the Tesseract binary) |
| Charts | Plotly |
| Assistant | Groq API |
| Storage | SQLite (auto-created, no server needed) |

---

## Quick start

### 1. Clone the repository

```bash
git clone https://github.com/kmroshan30/MediScan-AI---Copy.git
cd MediScan-AI---Copy
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Tesseract (required for the Medicine Scanner)

`pytesseract` is only a Python wrapper — it needs the Tesseract OCR engine installed
separately on your system.

**Windows**
1. Download the installer from [UB-Mannheim/tesseract-wiki](https://github.com/UB-Mannheim/tesseract/wiki).
2. Install it, then set the path so the app can find it:

```bash
setx TESSERACT_PATH "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

**macOS**

```bash
brew install tesseract
```

**Ubuntu / Debian**

```bash
sudo apt install tesseract-ocr
```

### 5. Add your Groq API key

The AI Assistant needs a free Groq key from [console.groq.com](https://console.groq.com).

Create `.streamlit/secrets.toml`:

```toml
GROQ_API_KEY = "gsk_your_key_here"
```

Or set it as an environment variable instead:

```bash
# Windows
setx GROQ_API_KEY "gsk_your_key_here"

# macOS / Linux
export GROQ_API_KEY="gsk_your_key_here"
```

> `.streamlit/` and `.env` are already in `.gitignore`, so your key stays local.

#### If you deploy to Streamlit Community Cloud

`.streamlit/` is gitignored, so the file above **never reaches a deployed app** —
that is what keeps your key out of GitHub, but it also means the hosted app has
no key at all until you give it one. Adding it to your local `secrets.toml` (or
a local `.env`) has no effect on `mediscan-ai-cdu.streamlit.app`.

For the hosted app, put the key in the dashboard instead:

1. Open [share.streamlit.io](https://share.streamlit.io) and select your app.
2. Go to **Settings → Secrets**.
3. Add exactly this line (the name must match `GROQ_API_KEY`, including case):
   ```toml
   GROQ_API_KEY = "gsk_your_key_here"
   ```
4. Press **Save**, then press **Rerun** — the app must restart to read the secret.

The assistant page shows which of these sources the running app actually found
(the key is masked, e.g. `gsk_pdR...LvXm`), plus a **Check Groq connection**
button that calls Groq's `/models` endpoint and confirms the key is accepted and
the configured model is available. If the assistant never answers, that button
distinguishes a missing key from a revoked key, a retired model, and a network
failure — locally and on the deployed app alike.

### 6. Run the app

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501`. The SQLite database is created automatically on
first run — register an account from the login screen to get started.

---

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | For the AI Assistant | Authenticates with the Groq API. Read from the environment or `.streamlit/secrets.toml`. |
| `TESSERACT_PATH` | Windows only | Absolute path to `tesseract.exe`. Not needed on macOS/Linux. |

Without `GROQ_API_KEY` the rest of the app still works; only the assistant is disabled. The
assistant page states exactly where a missing key belongs for the way that copy of the app
is being served, instead of failing silently.

---

## Project structure

```
.
├── app.py                     # Single-file Streamlit app: auth gate, 13 features, i18n
├── assets/
│   ├── style.css              # Design tokens, layout, component styles
│   ├── hero_doctor_reference.png
│   └── mediscan_logo.png
├── data/
│   ├── triage_data.csv        # Raw labelled symptom data
│   ├── cleaned_triage_data.csv# Output of modules/preprocess.py
│   ├── medicines.csv          # Local medicine fallback database
│   └── healthcare_prices.csv  # Price comparison data
├── database/
│   ├── database.py            # Schema creation + migrations
│   └── mediscan.db            # Created on first run (git-ignored)
├── models/
│   └── triage_model.pkl       # Trained pipeline (committed)
├── modules/
│   ├── medicine_search.py     # Concurrent openFDA / RxNorm / Wikipedia lookup
│   ├── preprocess.py          # Cleans triage_data.csv
│   └── train_model.py         # Trains and saves the triage model
├── tests/
│   └── test_medicine_search.py
├── DOCS.md/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── APP_FLOW.md
│   └── DESIGN.md
└── requirements.txt
```

---

## How it works

### Symptom triage

A scikit-learn `Pipeline` that combines three feature types before classifying urgency:

```
symptoms text ──▶ TF-IDF vectorizer ──┐
symptom flags ──▶ OneHotEncoder ─────┼──▶ StandardScaler ──▶ LogisticRegression ──▶ urgency
age group     ──▶ OneHotEncoder ──────┘
```

The trained pipeline is saved to `models/triage_model.pkl` and loaded at startup.

### Safety override

Before trusting the model, the input is checked against **14 emergency keywords** —
`severe chest pain`, `difficulty breathing`, `loss of consciousness`, `heavy bleeding`,
`seizure`, `anaphylaxis`, and others. A match forces the result to **Emergency**
regardless of what the classifier predicts, so a model miss can never downgrade a
critical symptom.

### Medicine identification

Two paths, both landing on the same result view:

1. **OCR** — upload a photo of a medicine strip; Tesseract extracts the printed name.
2. **Manual search** — type the name directly.

The name is then resolved by querying **openFDA**, **RxNorm**, and **Wikipedia**
concurrently, with a per-source deadline and an LRU cache so repeat lookups are instant.
The three sources are merged and scored into a single result showing generic name, form,
uses, warnings, and a description.

### AI assistant

Conversational Q&A through the Groq API. Requests are constrained by safety rules that
force short, clear answers and direct users to emergency care for anything urgent.
The assistant answers in English, हिन्दी, or తెలుగు.

The model is `openai/gpt-oss-20b`, a *reasoning* model: it spends part of its token
budget on a hidden `reasoning` field before writing the visible answer. `reasoning_effort`
is pinned to `low` so that stays a handful of tokens instead of swallowing the whole
budget and returning an empty reply. That parameter is only sent to models that accept it,
so pointing `GROQ_MODEL` at a normal chat model keeps working instead of failing with a 400.

Every reply goes through a retry loop, and a failure is always shown with its reason rather
than dropped — a blank bubble used to be the only symptom of a rejected key.

---

## Data sources

| Source | Used for |
|---|---|
| [openFDA](https://open.fda.gov/) | Drug labels, warnings, active ingredients |
| [RxNorm](https://www.nlm.nih.gov/medline/pubs/medrxnorm/) | Normalised drug names |
| Wikipedia | Plain-language drug descriptions |
| Groq | LLM inference for the assistant |
| India national emergency numbers | 112, 108, and friends, shown in-app |

---

## Regenerating the ML model

Only needed if you change the training data or the pipeline:

```bash
python modules/preprocess.py     # data/triage_data.csv  -> data/cleaned_triage_data.csv
python modules/train_model.py    # -> models/triage_model.pkl
```

---

## Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

`tests/test_medicine_search.py` covers the multi-source merge logic. The `_scratch_*`
files are manual, network-dependent smoke tests that call the real APIs and are not part
of the suite.

---

## Troubleshooting

**"pytesseract / Tesseract Not Found"**
Tesseract isn't installed, or `TESSERACT_PATH` is wrong. Verify with
`tesseract --version` in a terminal.

**The AI Assistant says it isn't configured**
`GROQ_API_KEY` isn't set. Restart the terminal after `setx` — environment variables
don't apply to already-open shells.

**The AI Assistant works on localhost but never replies on the deployed app**
This is the expected outcome of `.streamlit/` being gitignored: the local
`secrets.toml` is deliberately not deployed, so the hosted app has no key. Add
the key under **Settings → Secrets** in the Streamlit Cloud dashboard and press
Rerun — see [If you deploy to Streamlit Community Cloud](#5-add-your-groq-api-key).

**The AI Assistant answers on one page but not after a redeploy**
Streamlit Community Cloud wipes its container filesystem on every restart, and
the app builds a fresh SQLite database. Accounts and chat history created
before a restart are gone. This is a single-developer prototype limitation, not
a bug; see [Deployment notes](#deployment-notes).

**Port 8501 already in use**

```bash
streamlit run app.py --server.port 8502
```

**"Medicine database not found"**
`data/medicines.csv` is missing or was moved. It ships with the repo — re-download it if
you deleted it.

---

## Deployment notes

Deploying to Streamlit Community Cloud (`share.streamlit.io` → your app) needs three
things this project deliberately does not put in the repository:

| What | Why it isn't in the repo | Where to put it instead |
|---|---|---|
| `GROQ_API_KEY` | `.streamlit/` is gitignored, so a committed key would be public | Dashboard → **Settings → Secrets**, then **Rerun** |
| `TESSERACT_PATH` | Only meaningful on Windows; the cloud image installs its own Tesseract via `packages.txt` | Usually not needed |
| `MEDISCAN_DB_PATH` | Points at a durable volume on a self-hosted host | Only when you have a real disk |

Two consequences of running on Community Cloud are worth knowing before you file bugs:

- **The filesystem is ephemeral.** Every restart or redeploy throws away the SQLite
  database, including all accounts, chat history, reminders, and triage records. Users
  must register again. `MEDISCAN_DB_PATH` exists to redirect storage if you self-host
  somewhere with a persistent volume.
- **Secrets only apply after a restart.** Saving a secret in the dashboard does not
  affect the already-running container; press **Rerun**.

---

## Acknowledgements

Built with [Streamlit](https://streamlit.io), [scikit-learn](https://scikit-learn.org),
[Groq](https://groq.com), [openFDA](https://open.fda.gov), and the
[RxNorm](https://www.nlm.nih.gov/medline/pubs/medrxnorm/) vocabulary.

## Disclaimer

MediScan AI is an educational project. It does **not** provide medical diagnosis,
treatment, or professional advice, and must not be used in place of a qualified
healthcare provider. In an emergency, contact your local emergency services
immediately — in India, dial **112**.
