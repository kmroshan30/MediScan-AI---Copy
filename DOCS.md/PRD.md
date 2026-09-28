# Product Requirements Document (PRD) — MediScan AI

## 1. Executive Summary

**MediScan AI** is an educational, prototype healthcare companion application built with Streamlit. It combines symptom triage, medicine identification (via OCR and public drug database search), hospital price transparency, AI-powered health Q&A, medication reminders, and personal health record management into a single, multi-lingual, privacy-conscious web app.

The target audience is **individuals in India** seeking accessible, general health information and tools to navigate the healthcare system. The app is explicitly **not a diagnostic tool** and includes prominent disclaimers throughout.

---

## 2. Core Features & Requirements

### 2.1 Authentication & Account Management
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| AUTH-1 | User registration with username, name, email, password | P0 | PBKDF2-HMAC-SHA256 hashing (310k iterations) |
| AUTH-2 | Login with username or email | P0 | Legacy SHA-256 hashes upgraded on login |
| AUTH-3 | Password reset via local token (dev flow) | P1 | Token valid 15 min; shows in UI for dev |
| AUTH-4 | Refresh-proof session via HTTP-only cookie + server-side token store | P0 | 30-day TTL; survives page reload |
| AUTH-5 | Update username, email, password | P1 | Unique constraints enforced |
| AUTH-6 | Account deletion (cascades all user data) | P1 | Requires confirmation checkbox |

### 2.2 Symptom Triage (AI/ML)
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| TRI-1 | Text input for symptoms + age + duration + severity | P0 | Severity: Mild/Moderate/Severe |
| TRI-2 | ML model predicts urgency: Low / Moderate / High / Emergency | P0 | LogisticRegression + TF-IDF on symptoms |
| TRI-3 | Emergency keyword override (chest pain, breathing difficulty, etc.) | P0 | Bypasses model; forces "Emergency" |
| TRI-4 | Confidence score + probability distribution | P1 | From `predict_proba` |
| TRI-5 | General diet suggestion based on symptom keywords | P2 | 12 hardcoded keyword→tip mappings |
| TRI-6 | Persist triage history to SQLite per user | P0 | Includes timestamp, inputs, prediction |

### 2.3 Hospital Finder & Price Transparency
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| HOS-1 | Hierarchical location filter: City → Revenue District → District/Mandal | P0 | From `healthcare_prices.csv` |
| HOS-2 | Procedure search + select | P0 | Text filter on procedure names |
| HOS-3 | Best hospital card (highest rating, price tie-breaker) | P0 | Across revenue district scope |
| HOS-4 | In-district price summary (min/avg/max) | P1 | |
| HOS-5 | In-district hospital table with rating, price, phone, Google Maps link | P0 | |
| HOS-6 | Out-of-district suggestions within same revenue district | P1 | |
| HOS-7 | Charts: bar chart (price), scatter (rating vs price) | P2 | |
| HOS-8 | Savings calculation (highest vs lowest in district) | P2 | |

### 2.4 Medicine Scanner
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| MED-1 | Camera photo capture (Streamlit `camera_input`) | P0 | Requires Tesseract OCR + Pillow |
| MED-2 | Photo upload (PNG/JPG) | P0 | |
| MED-3 | OCR text extraction → fuzzy match against local `medicines.csv` | P0 | ~35 demo entries |
| MED-4 | Manual text search against local DB | P0 | Substring + difflib fuzzy |
| MED-5 | **Web search** across openFDA, RxNorm, Wikipedia (concurrent, deadline-bounded) | P0 | Key differentiator; finds Indian brands |
| MED-6 | Web search result rendering: facts grid, sources, Wikipedia summary, image | P0 | |
| MED-7 | "Search anywhere" links: Wikipedia, Google, 1mg, PharmEasy, MedPlus | P0 | |
| MED-8 | AI plain-language summary in user's language (English/Hindi/Telugu) | P1 | Uses Groq LLM + structured prompt |
| MED-9 | Set reminder / Save medicine actions | P1 | Links to Reminders & Saved Medicines |

### 2.5 AI Health Assistant (Chat)
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| AI-1 | Chat interface with Groq LLM (openai/gpt-oss-20b) | P0 | Requires `GROQ_API_KEY` |
| AI-2 | Reply languages: English, Hindi (हिन्दी), Telugu (తెలుగు) | P0 | Per-message language switch |
| AI-3 | Safety system prompt: no dosage, no diagnosis, disclaimer, <150 words | P0 | |
| AI-4 | One-tap suggestion chips in selected language | P1 | |
| AI-5 | Controls: Clear chat, Explain simply, Summarize, Regenerate, Translate last | P1 | |
| AI-6 | Voice input via `st.audio_input` | P2 | |
| AI-7 | Persist chat history to SQLite | P0 | |

### 2.6 Medication Reminders
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| REM-1 | Add reminder: name, form, timing (before/after food), time, notes | P0 | |
| REM-2 | In-app alert at scheduled minute: toast + notification + activity log | P0 | Fires once per minute per session |
| REM-3 | Client-side JS strip: beep (Web Audio), flashing red, next-reminder ETA | P0 | Polls every 10s via iframe |
| REM-4 | Sort by time; highlight due (±30 min) | P1 | |
| REM-5 | Clear all reminders | P1 | |
| REM-6 | Persist to SQLite; reload on login | P0 | |

### 2.7 Documents (Medical Records)
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| DOC-1 | Upload multiple files (PDF, PNG, JPG, WEBP) | P0 | Max 10 MiB each |
| DOC-2 | Store only metadata (name, type, size, timestamp) — **not contents** | P0 | Privacy by design |
| DOC-3 | List uploaded documents in table | P1 | |
| DOC-4 | Persist metadata to SQLite | P0 | |

### 2.8 History (Triage Records)
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| HIS-1 | Paginated table of past triage runs (timestamp, symptoms, age, severity, duration, urgency) | P0 | From SQLite |
| HIS-2 | Download as CSV | P1 | |

### 2.9 Profile & Emergency Contact
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| PRO-1 | Display name, username, emergency contact | P0 | |
| PRO-2 | Edit emergency contact (name, phone, relation) | P0 | Stored in `emergency_contacts` table |
| PRO-3 | Quick links to Security, Privacy, Logout | P1 | |

### 2.10 Notifications & Activity Log
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| NOT-1 | In-app notification center (success/warning/info) | P1 | Max 20, newest first |
| NOT-2 | Activity log (triage, reminders, saves) | P1 | Max 30 entries |
| NOT-3 | Clear notifications | P2 | |

### 2.11 Privacy & Security Center
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| PRI-1 | Explain local storage, password hashing, document handling | P1 | |
| PRI-2 | Clear stored documents & chat | P1 | |
| PRI-3 | Delete account (cascades all tables) | P1 | Requires confirmation |

### 2.12 Accessibility
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| ACC-1 | Larger text toggle (persisted) | P1 | CSS `font-size: 17px` |
| ACC-2 | High contrast toggle (persisted) | P1 | CSS `filter: contrast(1.12)` |
| ACC-3 | Visible focus outlines (`:focus-visible`) | P1 | |
| ACC-4 | `prefers-reduced-motion` respected | P2 | |

### 2.13 Internationalization (i18n)
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| I18N-1 | App UI labels: 12 languages (English, Hindi, Telugu, Bengali, Marathi, Tamil, Gujarati, Urdu, Kannada, Odia, Malayalam, Punjabi) | P0 | Dictionary in `TRANSLATIONS` |
| I18N-2 | AI Assistant replies: English, Hindi, Telugu | P0 | Separate prompt system |
| I18N-3 | Medicine web search: Wikipedia language follows assistant language | P1 | |

---

## 3. Non-Functional Requirements

| Category | Requirement |
|----------|-------------|
| **Performance** | Medicine web search completes in < 6s (concurrent openFDA + RxNorm + Wikipedia with deadlines) |
| **Reliability** | Graceful degradation: if any external API fails, show results from remaining sources |
| **Security** | PBKDF2-HMAC-SHA256 (310k iterations), salted; tokens stored as SHA-256 digests only |
| **Privacy** | No file contents stored; no analytics; no third-party trackers |
| **Usability** | Mobile-responsive CSS; dark mode via `prefers-color-scheme`; consistent design system |
| **Maintainability** | Modular code: `app.py` (UI), `modules/` (business logic), `database/` (schema + connection) |
| **Portability** | Runs on Windows/Linux/macOS; Tesseract path configurable via `TESSERACT_PATH` env var |

---

## 4. Data Sources & External Dependencies

| Source | Purpose | Access |
|--------|---------|--------|
| `data/triage_data.csv` | Training data for urgency classifier | Local CSV |
| `data/medicines.csv` | Demo medicine database (35 entries) | Local CSV |
| `data/healthcare_prices.csv` | Hospital/procedure price dataset | Local CSV (tab-separated) |
| openFDA Drug Labels API | Official US drug label data (purpose, warnings, side effects, etc.) | HTTPS, no key |
| RxNorm (NIH) | Normalized drug names, ingredients, related names | HTTPS, no key |
| Wikipedia REST API | Plain-language summaries, images | HTTPS, no key |
| Groq API | LLM for AI Assistant & medicine explanations | Requires `GROQ_API_KEY` |
| Google Maps | Hospital location search links | HTTPS, no key |
| Tesseract OCR | Local OCR engine (separate install) | System binary |

---

## 5. Constraints & Assumptions

- **Educational prototype only** — not for clinical use.
- **No background push notifications** — reminders only work while tab is open.
- **Demo medicine CSV is tiny** — web search is essential for real-world utility.
- **Healthcare prices are sample/estimated data** — not real provider data.
- **Groq API key required for AI features** — set via env var or `.streamlit/secrets.toml`.
- **Tesseract OCR must be installed separately** on the host system (not a pip package).
- **Single-user SQLite database** — not suitable for multi-user production deployment without migration.

---

## 6. Success Metrics (Prototype)

- All 13 main features load without error.
- Triage model achieves >80% accuracy on held-out test set.
- Medicine web search returns results for ≥90% of common Indian brand names tested.
- AI Assistant responds in selected language with safety disclaimer.
- Session persists across page reload (30-day cookie).
- Accessibility toggles apply immediately and persist.

---

## 7. Future Enhancements (Post-Prototype)

- Migrate to PostgreSQL + proper auth (OAuth2/OIDC).
- Background push notifications (service worker + Push API).
- Real hospital data integration (NHA/state APIs).
- Doctor appointment booking flow.
- Medicine interaction checker.
- Family/caregiver account linking.
- Offline-first PWA with service worker caching.
- FHIR-compliant health record export.