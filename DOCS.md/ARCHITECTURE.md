# Architecture — MediScan AI

## 1. System Overview

MediScan AI is a **single-process, server-rendered Streamlit web application** with a local SQLite database and optional external API integrations. It follows a **monolithic-with-modules** architecture: one primary entry point (`app.py`) orchestrates UI rendering and business logic, delegating persistence to `database/` and domain operations to `modules/`.

```
┌─────────────────────────────────────────────────────────────────┐
│                         Browser (Client)                        │
│  ┌───────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Streamlit UI  │  │  Cookie:     │  │ Iframe Components    │  │
│  │ (HTML/CSS)    │  │  session tok │  │ • Reminder beep JS   │  │
│  │               │  │              │  │ • Cookie write JS    │  │
│  └───────────────┘  └──────────────┘  └──────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ WebSocket (Streamlit protocol)
┌──────────────────────────────▼──────────────────────────────────┐
│                    Streamlit Server Process                     │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                        app.py  (~4,100 lines)              │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐ │  │
│  │  │ Auth Gate    │ │ Page Router  │ │ Business Logic     │ │  │
│  │  │ (register/   │ │ (13 features │ │ (triage, hospital │ │  │
│  │  │  login/reset)│ │  via active_ │ │  finder, scanner)  │ │  │
│  │  │              │ │  feature)    │ │                    │ │  │
│  │  └──────────────┘ └──────────────┘ └────────────────────┘ │  │
│  │  ┌──────────────────────────────────────────────────────┐  │  │
│  │  │  st.session_state (per-user state cache)             │  │  │
│  │  │  user_id, documents, chat_messages, reminders, etc.  │  │  │
│  │  └──────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────┐  ┌─────────────────────────────────────┐  │
│  │  modules/        │  │  database/                          │  │
│  │  ├── medicine_   │  │  ├── database.py                    │  │
│  │  │   search.py   │  │  │   • init_db() — schema + migrate │  │
│  │  │   (API merge) │  │  │   • get_connection()              │  │
│  │  ├── preprocess  │  │  └── mediscan.db                    │  │
│  │  │   .py (clean) │  │      (SQLite, local file)           │  │
│  │  ├── train_model │  └─────────────────────────────────────┘  │
│  │  │   .py (train) │  ┌─────────────────────────────────────┐  │
│  │  └── database.py │  │  models/                            │  │
│  │      (legacy)    │  │  └── triage_model.pkl               │  │
│  └──────────────────┘  │      (joblib: sklearn Pipeline)     │  │
│                        └─────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  data/                                                      │  │
│  │  ├── triage_data.csv          (raw training set)            │  │
│  │  ├── cleaned_triage_data.csv  (preprocessed)                │  │
│  │  ├── medicines.csv            (demo DB, ~35 rows)          │  │
│  │  └── healthcare_prices.csv    (TSV, hospital prices)        │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                     External Services (optional)                 │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐  │
│  │ openFDA    │  │ RxNorm     │  │ Wikipedia  │  │ Groq LLM │  │
│  │ (US FDA)   │  │ (NIH)      │  │ (MediaWiki)│  │ (LLM API)│  │
│  │ labels     │  │            │  │            │  │          │  │
│  └────────────┘  └────────────┘  └────────────┘  └──────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack

| Layer | Technology | Version Constraint |
|-------|-----------|-------------------|
| UI Framework | Streamlit | `>=1.37` |
| Data Processing | pandas, numpy | latest |
| ML / Model | scikit-learn, joblib | latest |
| OCR | pytesseract + Pillow | latest (+ system Tesseract) |
| LLM | groq (SDK) | latest |
| HTTP | requests | `>=2.31` |
| Database | sqlite3 (stdlib) | Python 3.13 |
| Styling | Custom CSS (`assets/style.css`) | — |
| Fonts | Google Fonts (Inter) | — |
| Runtime | Python 3.13+ | — |

---

## 3. Module Structure

### 3.1 `app.py` — Application Entry Point

The single Streamlit script. Structured in ordered sections:

| Section | Lines (approx) | Responsibility |
|---------|-------|---------------|
| Optional imports | 59–70 | Graceful degradation for OCR/Groq |
| Page config | 76–81 | `set_page_config` (must be first) |
| Paths | 87–99 | Resolve model/data/css paths |
| Password hashing | 102–215 | `hash_password`, `verify_password`, reset tokens |
| CSS loading | 218–242 | Inject `style.css` as `<style>` |
| API key setup | 253–273 | Tesseract path, `GROQ_API_KEY` |
| Assistant i18n | 289–575 | Safety rules, language dicts, prompt builders |
| Session restoration | 605–818 | Cookie-based refresh-proof sign-in |
| Auth gate | 921–1243 | Register / Login / Reset UI + `st.stop()` |
| Session state init | 1943–1986 | Feature data defaults |
| Data loaders | 1998–2193 | Read/write helpers for SQLite |
| Sidebar | 2254–2457 | Nav, account, language, emergency contact |
| Model load | 2463–2467 | `joblib.load` with error guard |
| Home dashboard | 2484–2572 | Greeting, hero, action cards, stats, activity |
| Reminder alerts | 2595–2728 | Server + client-side alerting |
| Page routers | 2755–4137 | 13 feature pages via `active_feature` |

**Key architectural patterns:**

- **Linear script, no routers/decorators.** Navigation is `st.session_state.active_feature` + `st.rerun()`.
- **`st.stop()` gate.** Everything after line 1243 runs only when `user_id` is set.
- **Result persistence in `session_state`.** Triage results, medicine lookups, AI summaries survive reruns (Streamlit buttons only return `True` for one run).
- **Helper `safe_text()`.** Escapes all user/DB/model text before injecting into raw HTML via `unsafe_allow_html=True`.

### 3.2 `modules/medicine_search.py` — External Medicine Lookup

The most sophisticated module. Merges results from three concurrent, deadline-bounded sources.

**Architecture:**

```
search_medicine(name, language, timeout)
    │
    ├─ @lru_cache(maxsize=256) _lookup_cached(name, language, timeout)
    │   │
    │   ├─ ThreadPoolExecutor(max_workers=3)
    │   │   ├─ lookup_openfda(name, timeout)        ← gating source
    │   │   │   ├─ _openfda_search_terms()         ← most identifying word first
    │   │   │   ├─ _openfda_wave()                 ← concurrent field queries
    │   │   │   ├─ _score_record()                 ← weighted name scoring
    │   │   │   └─ _parse_openfda()                ← condense label sections
    │   │   │
    │   │   ├─ lookup_wikipedia(name, lang, timeout)  ← gating source
    │   │   │   ├─ _wiki_terms()                   ← resolved names first
    │   │   │   ├─ _wiki_summary() per language    ← (te,en) or (hi,en) or (en)
    │   │   │   ├─ _looks_like_medicine()          ← reject place/person pages
    │   │   │   └─ _wiki_search() fallback         ← "term medicine"
    │   │   │
    │   │   └─ lookup_rxnorm(name, timeout)         ← opportunistic (non-gating)
    │   │       ├─ _rxnorm_candidates()            ← approximateTerm
    │   │       ├─ _rxnorm_properties()             ← rxcui properties
    │   │       └─ _rxnorm_related()                ← SCD/SBD related terms
    │   │
    │   └─ _merge(label, rxnorm, wiki, name)  ← single result dict
    │       └─ web_search_links()             ← 1mg/PharmEasy/MedPlus/etc
    │
    └─ Returns dict with same keys on success/failure (matched=False on failure)
```

**Key design decisions:**

- **Relevance scoring, not substring matching.** `is_relevant()` requires *every* meaningful token to appear as a whole word. Prevents "dolo" matching Wikipedia's "Dolophine" (methadone).
- **Weighted name scoring** (`_score_record`): brand=3.0, generic=2.5, substance=2.0; +4 exact match, +2 prefix; −0.25 per extra word (max 4). Threshold 2.0.
- **Per-stage deadlines:** `DEFAULT_TIMEOUT=3.0s` per request, `STAGE_DEADLINE=5.5s` total, `WIKI_RETRY_DEADLINE=3.0s` for the generic-name Wikipedia retry.
- **Dosage suppression:** `dosage_and_administration` is *never* parsed from openFDA labels.
- **LRU cache** (256 entries) prevents repeated network calls for the same name.

### 3.3 `database/database.py` — Schema & Connection

The **active** database module (imported by `app.py` as `from database.database import ...`).

**Provides:**
- `get_connection()` — `sqlite3.connect` with `row_factory = sqlite3.Row`
- `init_db()` — idempotent schema creation + migration
- `ensure_column()` — additive migration helper (adds columns to legacy tables)

**Schema (8 tables):**

| Table | Key Columns | Purpose |
|-------|------------|---------|
| `users` | `id`, `username` (unique), `name`, `email` (unique), `password_hash` | Accounts |
| `password_reset_tokens` | `token_hash` (PK), `user_id`, `expires_at`, `used_at` | One-use reset tokens |
| `triage_history` | `id`, `user_id`, `symptoms`, `age`, `severity`, `duration`, `predicted_urgency`, `created_at` | ML assessments |
| `sessions` | `id`, `user_id`, `token_hash`, `ui_state` (JSON), `expires_at`, `last_active` | Refresh-proof sign-in |
| `emergency_contacts` | `id`, `user_id` (unique), `name`, `phone`, `relation` | SOS contact |
| `medicine_scans` | `id`, `user_id`, `medicine_name`, `ocr_text`, `created_at` | Scan history (schema exists; writes not yet implemented in UI) |
| `saved_medicines` | `id`, `user_id`, `medicine_name`, `created_at` + unique index | Saved list |
| `reminders` | `id`, `user_id`, `medicine_name`, `form`, `food_timing`, `reminder_time`, `notes`, `is_active`, `created_at` | Medication schedule |
| `documents` | `id`, `user_id`, `filename`, `file_type`, `file_path` (NULL), `size_bytes`, `uploaded_at` | Upload metadata only |
| `chat_history` | `id`, `user_id`, `role`, `message`, `created_at` | AI conversation |

**Migration strategy:** `CREATE TABLE IF NOT EXISTS` + `ensure_column()` (via `PRAGMA table_info`) + `ALTER TABLE ADD COLUMN`. Idempotent; safe to run every startup.

### 3.4 `modules/database.py` — Legacy Module

**Not imported by `app.py`.** Older schema (3 tables: `users`, `triage_history`, `reminders` with different columns). Superseded by `database/database.py`. Should be considered dead code.

### 3.5 `modules/preprocess.py` — Data Cleaning

Script-mode (not imported). Reads `data/triage_data.csv`, lowercases symptoms, extracts `duration_days`, maps severity to numeric score, drops duplicates, writes `data/cleaned_triage_data.csv`.

### 3.6 `modules/train_model.py` — Model Training

Script-mode (not imported). Reads `cleaned_triage_data.csv`, builds sklearn `Pipeline`:

```
ColumnTransformer
├── symptoms   → TfidfVectorizer(max_features=1000, ngram_range=(1,2))
├── severity   → OneHotEncoder(handle_unknown="ignore")
└── numeric    → SimpleImputer(median) → StandardScaler
        ↓
LogisticRegression(max_iter=1000)
```

Train/test split: 80/20, `random_state=42`, stratified. Saved to `models/triage_model.pkl` via `joblib.dump`.

### 3.7 `assets/style.css` — Design System

929-line stylesheet. **Design tokens** in `:root` CSS variables; dark mode via `@media (prefers-color-scheme: dark)`. All component styles (buttons, cards, tables, sidebar, spinner, AI chat) are custom. Hero image is base64-injected at runtime (`__MEDISCAN_HERO_DOCTOR__` placeholder replaced in `load_css()`).

---

## 4. Data Flow Diagrams

### 4.1 Authentication & Session Flow

```
Browser                           Streamlit                    SQLite
   │                                  │                           │
   │──── page load ──────────────────▶│                           │
   │                                  │ restore_persist_session() │
   │                                  │── read cookie (st.context)│
   │◀─── cookie: mediscan_session ────│   _persist_token_hash()   │
   │                                  │── SELECT sessions JOIN ───▶│
   │                                  │◀── user_id, ui_state ─────│
   │                                  │                           │
   │   if user_id is None:            │                           │
   │   ┌──────────────────────┐       │                           │
   │   │ show login/register │       │                           │
   │   │ st.stop()           │       │                           │
   │   └──────────────────────┘       │                           │
   │                                  │                           │
   │──── login(identifier, pw) ─────▶│                           │
   │                                  │ hash/verify (PBKDF2)      │
   │                                  │── SELECT users ───────────▶│
   │                                  │◀── user row ──────────────│
   │                                  │ issue_persist_token()     │
   │                                  │── INSERT sessions ────────▶│
   │                                  │ _set_persist_cookie()     │
   │                                  │   (iframe JS writes)      │
   │◀─── 30-day cookie + rerun ──────│                           │
```

### 4.2 Triage Prediction Flow

```
User input                          app.py                     Model
   │                                   │                          │
   │ symptoms, age, duration,          │                          │
   │ severity                         │                          │
   │──── "Analyze" click ────────────▶│                          │
   │                                   │ parse duration → days    │
   │                                   │ severity → 1/2/3         │
   │                                   │ check emergency_keywords  │
   │                                   │                          │
   │                          if EMERGENCY:                      │
   │                                   │ prediction="Emergency"   │
   │                          else:     │                          │
   │                                   │── DataFrame ────────────▶│
   │                                   │   predict()              │
   │                                   │   predict_proba()         │
   │                                   │◀── prediction, probs ────│
   │                                   │                          │
   │                                   │ INSERT triage_history ──▶│
   │                                   │ log_activity()           │
   │                                   │ add_notification()       │
   │                                   │ store in session_state   │
   │                                   │ st.rerun()               │
   │◀─── rendered result ──────────────│                          │
   │    (urgency, confidence,          │                          │
   │     probabilities, diet tip)     │                          │
```

### 4.3 Medicine Web Search Flow

```
Query (typed or OCR text)
   │
   ├──▶ find_medicine(text, medicines_df)   ← local demo CSV (fast, offline)
   │       ├── 1) direct substring match
   │       └── 2) difflib fuzzy per line (cutoff 0.5)
   │
   └──▶ search_medicine(name, lang)         ← modules/medicine_search.py
           │
           ├──▶ openFDA  (concurrent, deadline 3.0s/req, 5.5s total)
           │       └── weighted name scoring, min 2.0
           ├──▶ Wikipedia (concurrent, lang-ordered te→en / hi→en / en)
           │       └── reject non-medicine pages
           └──▶ RxNorm    (opportunistic, not gating)
                   │
                   ▼
           _merge() → single dict + web_search_links()
                   │
           ├──▶ render_medicine_web_result()  ← facts grid + sources + image
           └──▶ render_medicine_ai_summary()  ← Groq LLM in user language
```

---

## 5. State Management

### 5.1 `st.session_state` — Runtime State (per browser session)

| Key | Type | Purpose |
|-----|------|---------|
| `user_id`, `user_name`, `username` | int/str/str | Auth identity |
| `active_feature` | str | Current page (one of 13) |
| `auth_page` | str | `"login"` or `"register"` |
| `triage_result` | dict | Persisted ML result |
| `documents` | list[dict] | Uploaded doc metadata |
| `chat_messages` | list[dict] | AI conversation |
| `reminders` | list[dict] | Medication schedule |
| `saved_medicines` | list[str] | Saved medicine names |
| `activity_log` | list[dict] | Recent events (max 30) |
| `notifications` | list[dict] | In-app notifications (max 20) |
| `assistant_language` | str | English / हिन्दी / తెలుగు |
| `medicine_lookup` | dict | Last web search result + query + language |
| `medicine_ai_summary` | dict | Cached LLM summary |
| `prefill_reminder` | str | Medicine name to prefill from scanner |
| `persist_token` | str | Browser session token |
| `reminder_alerts_fired` | set | Dedup key: `date|reminder_id|time` |
| `emergency_contact` | dict | name/phone/relation |

### 5.2 `st.context.cookies` — HTTP Cookie

- Name: `mediscan_session`
- Value: `secrets.token_urlsafe(32)` (only the SHA-256 digest is stored server-side)
- Attributes: `path=/; max-age=2592000; SameSite=Lax`

### 5.3 SQLite — Durable State

All records survive browser close. `load_local_user_data()` rehydrates `session_state` from SQLite on every run.

---

## 6. Security Architecture

| Concern | Mitigation |
|---------|-----------|
| **Password storage** | `hashlib.pbkdf2_hmac("sha256", pw, salt, 310_000)` → `pbkdf2_sha256$310000$salt$hash` |
| **Legacy hashes** | Transparent SHA-256 fallback in `verify_password`; upgraded to PBKDF2 on successful login |
| **Session tokens** | Only SHA-256 digest stored in `sessions.token_hash`; raw token in cookie |
| **Reset tokens** | `secrets.token_urlsafe(32)`, SHA-256 digest stored, 15-min expiry, one-use (`used_at`) |
| **Account enumeration** | `issue_local_reset_token()` returns a token-shaped value even for unknown emails; same UI flow regardless |
| **SQL injection** | All queries use parameterized `?` placeholders |
| **XSS** | `safe_text()` = `html.escape(str(value), quote=True)` applied to all interpolated user/DB/model content |
| **File storage** | Document *contents* are never written to disk — only metadata |
| **External links** | `rel="noopener noreferrer"` on `target="_blank"` links |
| **Phone numbers** | URL-encoded via `quote()` for `tel:` links |

---

## 7. Concurrency & Deadlines

### 7.1 Medicine Search Threading

```
ThreadPoolExecutor(max_workers=3)
├── thread A: lookup_openfda   ── awaited with remaining(5.5s)
├── thread B: lookup_wikipedia ── awaited with remaining(5.5s)
└── thread C: lookup_rxnorm    ── opportunistic (.done() check only)

if not wiki and label.generic_name:
    submit wiki retry with generic+substance names (3.0s deadline)
```

Any source that fails or times out contributes `{}`; the merge still succeeds.

### 7.2 Per-Request Timeouts

| Operation | Timeout |
|-----------|---------|
| HTTP GET (any source) | 3.0s |
| openFDA wave (3 concurrent queries) | 4.0s (`timeout + 1.0`) |
| Wikipedia per language | 3.0s |
| Stage total (openFDA + wiki) | 5.5s |
| Wikipedia generic-name retry | 3.0s |
| Reminder JS poll interval | 10s |
| Reminder day rollover check | 60s |

### 7.3 Client-Side Reminder Strip

A `components.html` iframe (same-origin, real JS) is injected on every logged-in page. It:
1. Receives reminder JSON via `__REMINDERS__` placeholder replacement
2. Ticks every 10 seconds
3. On due minute: plays a 5-pulse Web Audio beep (880 Hz sine), turns strip red/flashing
4. Between reminders: shows next reminder name + time
5. Resets `fired` map on date rollover

---

## 8. Error Handling Strategy

| Layer | Strategy |
|-------|----------|
| **Optional imports** | `try/except ImportError` → `OCR_AVAILABLE`, `GROQ_LIB_AVAILABLE` flags |
| **Model load** | `try/except` → `st.error` + `st.stop()` |
| **Data files** | `try/except FileNotFoundError` → actionable error message |
| **DB reads** | Per-group `query()` helper; one failure warns without blanking other records |
| **DB writes** | `_write()` returns `None` + `st.warning` on failure; never crashes the page |
| **External APIs** | `_get_json` returns `None` on any exception; `_safe_call` wraps every job |
| **Groq LLM** | `try/except` → error shown in chat bubble; feature degrades gracefully |
| **OCR** | `try/except` → "install Tesseract" instructions + exception details |
| **Session restore** | All wrapped in `try/except`; failure is silent (no crash) |
| **Cookie writes** | Deferred to next run via `persist_cookie_clear_pending` flag |

---

## 9. Deployment Architecture

### 9.1 Local Development

```bash
# 1. Install Tesseract OCR (system binary — separate from pip)
#    Windows: https://github.com/UB-Mannheim/tesseract/wiki
#    Linux:   sudo apt install tesseract-ocr
#    macOS:   brew install tesseract

# 2. Set environment variables
export TESSERACT_PATH="/path/to/tesseract"    # Windows only, optional
export GROQ_API_KEY="gsk_..."                 # for AI Assistant

# 3. Python dependencies
pip install -r requirements.txt

# 4. (Optional) Retrain the triage model
python modules/preprocess.py
python modules/train_model.py

# 5. Run
streamlit run app.py
```

### 9.2 Streamlit Cloud / Cloud Server Considerations

- First run may take 1–2 minutes (scikit-learn import + model load). App shows a caption warning.
- SQLite is ephemeral on cloud hosts (ephemeral filesystem) — data lost on redeploy.
- `requirements.txt` covers all pip deps. Tesseract must be added via `packages.txt` or OS-level install.
- Secrets via `.streamlit/secrets.toml` or Streamlit Cloud secrets manager.

### 9.3 Production Gaps

| Gap | Current State | Required for Production |
|-----|--------------|--------------------------|
| Database | Local SQLite file | PostgreSQL + migrations (Alembic) |
| Auth | Local password + cookie | OAuth2/OIDC, MFA, rate limiting |
| Sessions | SQLite-backed tokens | Redis / JWT with rotation |
| File uploads | Metadata only | S3/blob storage + virus scan |
| Rate limiting | None | Per-IP / per-user limits |
| Audit log | Activity log (in-memory) | Append-only, tamper-evident log |
| HTTPS | Streamlit default | TLS termination + HSTS |
| Secrets | Env vars | Vault / KMS |

---

## 10. Architectural Decisions & Trade-offs

### AD-1: Single-file Streamlit app vs. framework
**Decision:** Single `app.py` (~4,100 lines) with clear section banners.
**Trade-off:** Fast to develop/demo; hard to unit-test or scale. Mitigated by delegating persistence (`database/`) and domain logic (`modules/medicine_search.py`) to testable modules.

### AD-2: SQLite vs. server database
**Decision:** SQLite file at `database/mediscan.db`.
**Trade-off:** Zero setup, no credentials, works offline. But single-writer, no concurrent users, ephemeral on cloud. Acceptable for an educational prototype.

### AD-3: Local ML model vs. hosted inference
**Decision:** Pre-trained `triage_model.pkl` loaded via `joblib`.
**Trade-off:** Instant, private, no API cost. But requires retraining when data changes, no A/B testing, and the "model card" is just a LogisticRegression on a small CSV.

### AD-4: Rule-based emergency override vs. pure ML
**Decision:** 14 hardcoded emergency keywords bypass the model.
**Trade-off:** False positives possible (e.g., "mild chest pain" → Emergency). But safer than relying on a model trained on a small dataset to catch life-threatening symptoms.

### AD-5: Concurrent external APIs with deadlines vs. sequential
**Decision:** `ThreadPoolExecutor` with per-stage deadlines.
**Trade-off:** More code, but the page never blocks on a slow source. Partial results are always shown.

### AD-6: LRU cache for medicine lookups
**Decision:** `@lru_cache(maxsize=256)` on `_lookup_cached`.
**Trade-off:** Stale results if a source updates. But avoids hammering public APIs and makes repeat searches instant.

### AD-7: Suppress dosage from openFDA labels
**Decision:** `dosage_and_administration` field is never parsed.
**Trade-off:** Users can't see official dosing from the label. But the app's safety policy is "never give a personal dosage amount" — this is enforced at the parsing layer, not just the prompt layer.

### AD-8: Client-side cookie write via iframe
**Decision:** `components.html()` with `document.cookie` JS in a 0-height same-origin iframe.
**Trade-off:** Hacky but works. Streamlit has no server-side cookie API. Alternative (`st.context.cookies` is read-only) requires the browser to write.

### AD-9: No background service worker
**Decision:** Reminders only work while the tab is open (JS poll + server rerun check).
**Trade-off:** Simpler, no PWA complexity. But users must keep the tab open for reliable alerts.

### AD-10: Hardcoded diet suggestions
**Decision:** 12 keyword→tip mappings in `DIET_SUGGESTIONS`.
**Trade-off:** Feels personalized but is a lookup table, not nutrition science. Mitigated by explicit `DIET_DISCLAIMER` and "general wellness information only" copy.

---

## 11. Scalability Considerations

| Dimension | Current Limit | Scaling Path |
|-----------|---------------|--------------|
| Concurrent users | ~1–5 (SQLite single-writer) | Migrate to PostgreSQL |
| Medicine search latency | 5.5s worst case | Add Redis cache layer; precompute common brands |
| Model inference | ~50ms (LogisticRegression) | Not a bottleneck |
| Database size | Unlimited (file-based) | Partition by date if history grows |
| Static assets | Inlined in CSS/HTML | Move to CDN |

---

## 12. Security Threat Model

| Threat | Mitigation | Residual Risk |
|--------|-----------|---------------|
| SQL injection | Parameterized queries everywhere | None in current code |
| XSS | `safe_text()` on all interpolated content; `unsafe_allow_html` used deliberately | Requires vigilance when adding new HTML blocks |
| Session fixation | New token on every login; digest-only storage | Cookie not `HttpOnly` (iframe limitation) |
| Account enumeration | Uniform reset flow; generic error messages | Timing side-channels possible |
| Brute force | No rate limiting | **Gap** — add in production |
| Credential stuffing | PBKDF2 with 310k iterations | Password policy: min 8 chars (weak) |
| Data exfiltration | No third-party analytics; no file contents stored | External APIs receive search queries |
| Session replay | 30-day TTL; digest-only storage | Long TTL window |
| DoS | No request limits; LRU cache bounded | **Gap** — add in production |

---

## 13. Future Architecture Evolution

```
Current (Prototype)                    Target (Production)
──────────────────                     ─────────────────────
Streamlit single process    →          FastAPI backend + React frontend
SQLite file                 →          PostgreSQL + Redis
Local PBKDF2 auth           →          OAuth2/OIDC + MFA
joblib sklearn model        →          Model registry + versioning
In-session reminders        →          Background worker + Push API
Inline CSS                  →          Design system package (Storybook)
Direct API calls            →          Backend proxy + circuit breaker
No observability            →          Structured logging + tracing
No tests in CI              →          pytest + coverage gates
```
