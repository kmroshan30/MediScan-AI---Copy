# Application Flow — MediScan AI

This document traces every user journey and the internal execution order of `app.py`.

---

## 1. Startup Sequence (Every Streamlit Rerun)

Streamlit re-executes the entire script top-to-bottom on every interaction. The order below is fixed and load-bearing.

```
1.  import streamlit, pandas, joblib, sqlite3, groq, ...
2.  try: import pytesseract, PIL      → OCR_AVAILABLE
    try: import groq                   → GROQ_LIB_AVAILABLE
3.  st.set_page_config(...)            ← must be the FIRST Streamlit command
4.  Resolve BASE_DIR + paths
       MODEL_PATH      = models/triage_model.pkl
       PRICE_DATA_PATH = data/healthcare_prices.csv
       MEDICINE_DATA_PATH = data/medicines.csv
       CSS_PATH        = assets/style.css
       LOGO_PATH       = assets/mediscan_logo.png
5.  init_db()                          ← creates/migrates SQLite schema
6.  Define password/reset helpers
7.  load_css(CSS_PATH)                 ← injects <style> block
8.  Configure Tesseract path (env TESSERACT_PATH)
9.  Resolve GROQ_API_KEY (env → st.secrets → None)
10. Build assistant language dictionaries
11. Initialise auth session_state defaults
12. restore_persist_session()          ← cookie → session rehydration
13. Emit/defer persist cookie write or clear
14. Clear stale widget state (after logout)
15. ─────── AUTH GATE ───────
    if user_id is None:
        render login / register / reset UI
        st.stop()                     ← nothing below runs
16. Render branded logo row
17. Apply accessibility overrides (large text / high contrast)
18. Define TRANSLATIONS dictionary (12 languages)
19. Initialise feature session_state defaults
20. load_local_user_data()             ← rehydrate documents/chat/reminders/…
21. Define write helpers, log_activity(), add_notification(), safe_text()
22. Render sidebar (nav, account, language, emergency contact)
23. joblib.load(MODEL_PATH)            ← st.stop() on failure
24. Render main header (non-Home pages)
25. Render Home dashboard (if active)
26. Reminder alert loop (server + client JS)
27. Render Back button + breadcrumb (non-Home)
28. Route to the active feature page
```

> **Key invariant:** the auth gate at step 15 uses `st.stop()`. Every feature page below it is unreachable for anonymous visitors.

---

## 2. Authentication Flows

### 2.1 Registration

```
Register form (username, full name, email, password)
   │
   ├── all fields empty?            → warning "Please fill all fields."
   ├── password < 8 chars?          → warning "Use a password with at least 8 characters."
   ├── username fails regex?        → warning "Username must be 3–30 characters
   │      [a-z0-9_]{3,30}                using letters, numbers, or underscores."
   └── valid:
        username = lower(strip)
        email    = lower(strip)
        password_hash = pbkdf2_sha256(password)
        │
        └── INSERT INTO users (username, name, email, password_hash)
             │
             ├── IntegrityError (UNIQUE username or email) → error
             └── success → st.success → auth_page = "login" → st.rerun()
```

### 2.2 Login

```
Login form (identifier, password)
   │
   identifier = lower(strip)
   SELECT id, username, name, password_hash
     FROM users
    WHERE lower(username) = ? OR lower(email) = ?
   │
   ├── no user OR verify_password fails → error "Invalid username/email or password."
   └── success:
        ├── if legacy SHA-256 hash → re-hash with PBKDF2, UPDATE users
        ├── load emergency_contacts row → session_state
        ├── active_feature = "Home"
        ├── token = secrets.token_urlsafe(32)
        ├── INSERT INTO sessions (user_id, token_hash, ui_state, expires_at)
        ├── set cookie (via iframe JS): mediscan_session=<token>, 30d, SameSite=Lax
        └── st.rerun()
```

### 2.3 Password Reset (development flow)

```
① "Send Reset Code"  (Registered Email)
      email = lower(strip)
      raw_token = secrets.token_urlsafe(32)
      SELECT id FROM users WHERE lower(email) = ?
      if found:
          UPDATE password_reset_tokens SET used_at = now
             WHERE user_id = ? AND used_at IS NULL
          INSERT INTO password_reset_tokens (token_hash, user_id, expires_at)
             expires_at = now + 900  (15 minutes)
      st.info("Development reset token (valid for 15 minutes): <token>")
      ─────────────────────────────────────────────────────────────
      NOTE: a token-shaped value is returned even for unknown emails,
            so the flow never reveals whether an account exists.

② "Verify Code"  (Enter Reset Code)
      SELECT t.user_id
        FROM password_reset_tokens t
        JOIN users u ON u.id = t.user_id
       WHERE t.token_hash = sha256(entered_code)
         AND lower(u.email) = ?
         AND t.used_at IS NULL
         AND t.expires_at > now
      │
      ├── no row → error "Invalid or expired reset code."
      └── row + exactly 1 row updated → reset_verified = True

③ "Reset Password"  (New Password + Confirm)
      ├── either field empty     → warning
      ├── new != confirm         → error "Passwords do not match."
      └── valid:
           UPDATE users SET password_hash = pbkdf2(new)
           UPDATE password_reset_tokens SET used_at = now
           clear reset_* session keys
           st.success("Password reset successfully.")
```

> **Production note:** the reset token is printed in the UI because there is no email provider. In a hosted deployment this value would be emailed/SMS'd instead.

### 2.4 Refresh-Proof Sign-In

```
On page load:
   token = st.context.cookies.get("mediscan_session")
   │
   ├── no token → remain logged out
   └── token present:
        SELECT s.user_id, s.ui_state, u.name, u.username
          FROM sessions s JOIN users u ON u.id = s.user_id
         WHERE s.token_hash = sha256(token) AND s.expires_at > now
        │
        ├── no row (unknown / expired / account deleted) → logged out
        └── row:
             session_state.user_id / user_name / username restored
             ui_state JSON → active_feature (validated against
                             PERSISTED_FEATURES allow-list)
                           → accessibility flags
             (emergency_contact intentionally NOT restored here;
              load_local_user_data() reads the same row later)
```

### 2.5 Logout

```
Logout button
   drop_persist_token()
       ├── pop persist_token / persist_cookie_written
       ├── set persist_cookie_clear_pending = True
       ├── clear cookie (iframe JS: max-age=0)
       └── DELETE FROM sessions WHERE token_hash = sha256(token)
   clear_local_session_state()
       ├── user_id / user_name / username → None
       ├── clear_visitor_widgets_on_next_run = True
       ├── auth_page = "login"
       ├── documents / chat_messages / reminders / activity_log /
       │   saved_medicines / notifications / triage_result → emptied
       ├── assistant_language → "English"
       ├── medicine_lookup / medicine_ai_summary → None
       ├── reminder_alerts_fired → empty set
       └── active_feature → "Home"
   st.rerun()
   │
   └── next run: cookie clear retried, widget keys purged
```

---

## 3. Page Routing

Navigation is driven by a single session key.

```
st.session_state.active_feature
   │
   ├── "Home"                → Dashboard (hero, actions, stats, activity)
   ├── "Search"              → Global Search
   ├── "Triage"              → Symptom Triage
   ├── "Medicine Scanner"    → OCR + manual + web search
   ├── "AI Assistant"        → Chat
   ├── "Hospital Finder"     → Price transparency
   ├── "Reminders"           → Medication schedule
   ├── "History"             → Past triage records
   ├── "Documents"           → Upload metadata
   ├── "Profile"             → Account overview
   ├── "Notifications"       → Notification centre
   ├── "Privacy & Security"  → Data controls
   ├── "Accessibility"       → Display toggles
   └── "Loading Spinners"    → Component gallery
```

**Navigation mechanics:**

```
Sidebar button click
   → active_feature = page_name
   → save_persist_ui_state()      # write ui_state JSON to sessions row
   → st.rerun()

Dashboard "Open <feature>" button
   → active_feature = target
   → st.rerun()

Hero CTA / scanner "Set Reminder"
   → active_feature = "Triage" / "Reminders"
   → prefill_reminder = medicine_name (for Reminders)
   → st.rerun()

Back button (any non-Home page)
   → active_feature = "Home"
   → st.rerun()

Page reload
   → restore_persist_session() reads ui_state
   → active_feature restored ONLY if in PERSISTED_FEATURES
```

**Allowed `active_feature` values (allow-list):**
`Home, Search, Triage, Medicine Scanner, AI Assistant, Hospital Finder, Reminders, History, Documents, Profile, Notifications, Privacy & Security, Accessibility, Loading Spinners`

A stale or hand-edited value outside this list is discarded, so the visitor can never land on a blank screen.

---

## 4. Home Dashboard Flow

```
1. hour = now().hour
   greeting = "Good morning"   (h < 12)
           | "Good afternoon"   (h < 17)
           | "Good evening"

2. recent_triage  = get_recent_triage(limit=3)   → last 3 rows from triage_history
   reminder_count = len(session_state.reminders)
   document_count = len(session_state.documents)
   recent_count   = len(recent_triage) + len(activity_log)
   status         = recent_triage[0].predicted_urgency  or  "No recent check"

3. Render:
   ├── welcome block (eyebrow, greeting + name, tagline, live-status pill)
   ├── hero panel (headline, description, doctor image, trust badge)
   ├── 2 CTA buttons  → Triage | Hospital Finder
   ├── "How can we help you today?" — 6 action cards in a 3-column grid
   │     Symptom Triage · Medicine Scanner · AI Assistant
   │     Hospital Finder · Reminders · History
   ├── "Today's Health" — 3 stat cards
   │     Reminders count · Recent records count · Health status (latest urgency)
   ├── "Recent Activity" — merge of last 3 triage rows + 5 activity_log entries
   │     (empty state if none)
   └── "Health Tips for You" — static general wellness copy
```

---

## 5. Symptom Triage Flow

### 5.1 Input stage

```
Text area  → symptoms           (free text, e.g. "fever and cough for 3 days")
Number     → age               (1–120, default 22)
Text input → duration          (free text, e.g. "3 days")
Select     → severity          (Mild | Moderate | Severe)
```

### 5.2 Analysis

```
[🔍 Analyze Symptoms] clicked
   │
   ├── symptoms blank → warning "Please enter your symptoms before analyzing."
   │
   └── symptoms present:
        duration_days   = first integer in duration   (default 1)
        severity_score  = {Mild:1, Moderate:2, Severe:3}[severity]
        is_emergency    = any(keyword in symptoms.lower()
                               for keyword in emergency_keywords)
        │
        ├── build DataFrame:
        │     symptoms, age, severity, duration_days, severity_score
        │
        ├── if is_emergency:
        │     prediction   = "Emergency"
        │     confidence   = 100.0
        │     probabilities = None ; classes = None
        │
        └── else:
              prediction   = model.predict(df)[0]
              probabilities = model.predict_proba(df)[0]   (if available)
              confidence   = max(probabilities) * 100
              classes      = list(model.classes_)

        diet_tip = first DIET_SUGGESTIONS key found in symptoms.lower()
                   (fever, diabetes, sugar, acidity, gastric, cold, cough,
                    diarrhea, vomiting)

        INSERT INTO triage_history
           (user_id, symptoms, age, severity, duration, predicted_urgency)
        log_activity("Symptom assessment", "Triage", prediction)
        add_notification("New triage report available", "success")
        st.session_state.triage_result = {...}
        st.rerun()
```

**Emergency keywords (14):** severe chest pain, chest pain, difficulty breathing, trouble breathing, shortness of breath, unconscious, loss of consciousness, heavy bleeding, uncontrolled bleeding, seizure, sudden weakness, one-sided weakness, severe allergic reaction, anaphylaxis.

### 5.3 Result rendering (separate from the button)

```
triage_result exists?
   │
   ├── Yes → render:
   │     ├── urgency banner
   │     │     Emergency → st.error  🚨
   │     │     High     → st.error  🔴
   │     │     Moderate → st.warning 🟡
   │     │     Low      → st.success 🟢
   │     │     other    → st.info
   │     ├── "Symptoms entered" (raw text)
   │     ├── if is_emergency: red safety block + st.metric("Safety Override")
   │     ├── confidence → st.write + st.progress
   │     ├── per-class probability bars
   │     ├── if diet_tip: diet suggestion + DIET_DISCLAIMER
   │     ├── educational-prototype caption
   │     └── [➕ New Analysis] → triage_result = None → st.rerun()
   │
   └── No → render the input form
```

> **Why the result is stored in session_state:** Streamlit buttons return `True` only for the single run triggered by the click. If the output lived inside `if st.button(...)`, any later rerun (a sidebar toggle, a form edit) would wipe the analysis off the screen.

---

## 6. Hospital Finder Flow

```
pd.read_csv(PRICE_DATA_PATH, sep="\t")  → columns stripped
   │
   │  FileNotFoundError → error "Healthcare price dataset not found…"
   │  other Exception  → error "Unable to load healthcare price dataset: …"
   │
   ├── ① City            = selectbox(sorted(unique))
   ├── ② Revenue District= selectbox(sorted(unique))   ← only if city has > 1 region
   ├── ③ District/Mandal = selectbox(sorted(unique))
   ├── ④ Procedure filter= text_input  → substring match
   │      no match → warning + fall back to full list
   ├── ⑤ Procedure       = selectbox(filtered)
   │
   ├── scope  = rows where procedure == selected          (revenue-district scope)
   ├── local  = scope where district  == selected district  (IN district)
   ├── other  = scope where district != selected district  (OUT of district)
   │
   ├── BEST HOSPITAL CARD
   │     sort scope by [rating DESC, price_inr ASC] → first row
   │     show provider, district, ⭐ rating, ₹ price, tel: link, Maps link
   │     if best.district != selected_district → caption warning
   │
   ├── PRICE SUMMARY (in-district only)
   │     st.metric Lowest / Average / Highest price
   │
   ├── IN-DISTRICT TABLE
   │     columns: Hospital, Price (₹), Rating, Phone, Map (LinkColumn)
   │     empty → warning "No hospitals found in this district for this procedure."
   │
   ├── OUT-OF-DISTRICT TABLE
   │     columns: Hospital, District, Price (₹), Rating, Phone, Map
   │     empty → "No other districts found in this revenue district."
   │
   ├── CHARTS
   │     bar chart   : price by provider (in-district)
   │     scatter     : rating vs price (whole scope)
   │
   ├── POTENTIAL SAVINGS
   │     savings         = max_price - min_price
   │     savings_percent = savings / max_price * 100
   │
   └── SAMPLE-DATA CAPTION
         prices/ratings/phones are sample data;
         map links open a Google Maps SEARCH, not a pinned coordinate
```

---

## 7. Medicine Scanner Flow

```
medicines_df = pd.read_csv(MEDICINE_DATA_PATH)
   FileNotFoundError → medicines_df = None + error message
   │
┌────────────────────────────┬──────────────────────────────────────┐
│ LEFT COLUMN: Photo Scan    │ RIGHT COLUMN: Type the Name          │
├────────────────────────────┼──────────────────────────────────────┤
│ !OCR_AVAILABLE →           │ text_input "Medicine name"           │
│   install instructions     │ toggle    "Search the web" (default on)│
│                            │ [🔍 Identify Medicine]                │
│ camera_input (live photo)  │                                      │
│ file_uploader png/jpg/jpeg │                                      │
│                            │                                      │
│ image = Image.open(source) │                                      │
│ inline loader shown        │                                      │
│ text = pytesseract          │                                      │
│         .image_to_string() │                                      │
│ loader cleared             │                                      │
│ expander: raw OCR text     │                                      │
│                            │                                      │
│ local = find_medicine()    │                                      │
│   1. substring match       │                                      │
│   2. difflib per line      │                                      │
│      (cutoff 0.5)          │                                      │
│                            │                                      │
│ if web toggle on:          │                                      │
│   session.medicine_lookup  │ local = find_medicine(typed, df)     │
│     = {query: text[:200],  │ session.medicine_lookup              │
│        web: True,          │   = {query, web: toggle,             │
│        language: <code>,   │      language, result: None}          │
│        result: search_     │ session.medicine_ai_summary = None    │
│                 medicine()}│                                      │
│ session.medicine_ai_summary│ if web toggle on:                     │
│        = None              │   spinner "Searching public…"         │
│                            │   lookup.result = search_medicine()  │
│ local miss → warning       │   no match  → warning                │
│                            │   web only  → info                   │
└────────────────────────────┴──────────────────────────────────────┘
   │
   ├── LOCAL RESULT CARD (if matched)
   │     Purpose · Composition · Form · Drug class · Typical timing · Price
   │     warning "This scanner does not determine a personal dosage."
   │     [Set Reminder] → Reminders page with prefill
   │     [Save to Medicines] → INSERT OR IGNORE saved_medicines
   │
   └── WEB RESULT BLOCK (full width, from session_state)
         │
         ├── language changed since lookup?
         │     yes → re-run search_medicine() (Wikipedia is lang-ordered)
         │
         ├── render_medicine_web_result(result, query)
         │     ├── no match → "No database match" card + explanation
         │     └── matched  → title, source line, confidence banner
         │                   (partial → amber "closest match" warning)
         │                   Wikipedia image, summary, facts grid
         │                   Purpose · Uses · Active ingredient · Generic name
         │                   Substance · Form · Route · Manufacturer
         │                   Matched as · Also known as · Warnings
         │                   Side effects · Precautions · Interactions
         │     caption naming openFDA / RxNorm / Wikipedia
         │     "Search this medicine anywhere" links:
         │        Wikipedia · Google · Google Images
         │        1mg · PharmEasy · MedPlus
         │
         └── render_medicine_ai_summary(result, query)
               no groq key/package → caption, stop
               cached for (query, language) → render cached
               [Explain in <language>] →
                  inline loader
                  Groq(system=ASSISTANT_SAFETY_RULES,
                       user=build_medicine_prompt(query, result, language),
                       max_tokens=320, temperature=0.3)
                  store in session.medicine_ai_summary → st.rerun()
```

---

## 8. AI Assistant Flow

```
① Reply-language switch
     selectbox labelled in the CURRENT reply language
     options: English | हिन्दी | తెలుగు   (shown in native script)
     change → assistant_language_manual = True
              medicine_ai_summary = None   (invalidate cached summary)

② Availability gate
     !GROQ_LIB_AVAILABLE → warning "pip install groq"
     GROQ_API_KEY is None → warning "set GROQ_API_KEY"
     else → chat UI

③ Empty chat → welcome card
     icon · welcome headline · body · 3 topic chips · "Ready to help"
     3 one-tap suggestion buttons (written in the selected language)
        click → session.ai_suggested_question = suggestion → st.rerun()

④ Control row (6 columns)
     [Clear chat]     → DELETE chat_history for user; chat_messages = []
     [Explain simply] → session.ai_prefill = "Explain your last answer…"
     [Summarize]      → session.ai_prefill = "Summarize in 3 bullets…"
     [Regenerate]     → if last msg is assistant:
                          delete_last_chat_message()  (DB)
                          chat_messages.pop()        (memory)
                        session.ai_regenerate = True → st.rerun()
     [Translate]      → disabled unless an assistant message exists
                        Groq(system=build_translation_prompt(last_answer, lang),
                             max_tokens=400, temperature=0.2)
                        replaces the last assistant message in place
                          (UPDATE chat_history) or appends a new one (INSERT)
                        → st.rerun()
     [Voice input]    → st.audio_input (recorded; not transcribed)

⑤ Message loop
     for msg in chat_messages:
         role == "user"      → .ai-user-message
         role == "assistant" → .ai-assistant-message

⑥ Input resolution (priority order)
     1. session.ai_prefill          (Explain / Summarize)
     2. st.chat_input(placeholder)
     3. session.ai_suggested_question  (one-tap chip)
     4. prompts["regenerate"]          (Regenerate flag)

⑦ Send
     append {"role":"user"} → chat_messages
     INSERT chat_history (user)
     inline loader "MediScan AI is thinking…"
     Groq(model="openai/gpt-oss-20b",
          messages=[{system: build_assistant_prompt(reply_language)}]
                   + chat_messages,
          max_tokens=400, temperature=0.4)
     append {"role":"assistant", content: reply} → chat_messages
     INSERT chat_history (assistant)
     st.rerun()
     on exception → reply = "Assistant error: <exc>" (still stored)
```

**Safety rules (system prompt, always English):** friendly cautious health-information assistant; simple general questions only; never give specific dosage amounts; never diagnose; always remind that this is general information, not medical advice; consult a doctor or pharmacist; seek emergency care for anything serious; keep answers short. Plus per-language instruction and "keep the whole reply under 150 words."

**Medicine names stay in Latin script** in every output language — translating a brand name makes it unsearchable and can point at a different product.

---

## 9. Medication Reminders Flow

### 9.1 Adding

```
[Add Reminder] form
   medicine name   (pre-filled from scanner via session.prefill_reminder)
   form            (Tablet | Capsule | Syrup | Liquid | Injection | Other)
   timing          (Before Food | After Food | Either / Not applicable)
   time            (st.time_input, default 09:00)
   notes           (optional)
   │
   └── name non-empty:
        id = save_reminder(name, form, food, "%H:%M:%S", notes)   → INSERT
        append to session.reminders
        log_activity("Reminder added: <name>", "Reminder", "<h:mm AM/PM>")
        add_notification("Medication reminder added for …", "success")
        st.toast("⏰ Reminder added for <name>.")
        st.rerun()
```

### 9.2 Rendering

```
sort reminders by time ascending
for each reminder:
   is_due = |reminder_minutes - now_minutes| <= 30
   card class = reminder-due (amber) if is_due else reminder-card
   body  = <name> (<form>) — <time>
           <food timing>
           <notes>          (italic, if present)
           "Due now"        (bold, if due)

[Clear All Reminders] → DELETE reminders for user → session = [] → st.rerun()
no reminders → st.info("No reminders added yet.")
```

### 9.3 Alerting (two cooperating layers)

```
LAYER 1 — Server (every rerun)
   for each reminder:
      occurrence_key = "<date>|<reminder_id>|HH:MM"
      if HH:MM == now and key not in reminder_alerts_fired:
          add_notification("⏰ Medication reminder due now: <name>", "warning")
          log_activity("Reminder due: <name>", "Reminder", "<time>")
          st.toast("⏰ <name> — time to take your medicine")
          reminder_alerts_fired.add(occurrence_key)

LAYER 2 — Client (components.html iframe, every page, while tab is open)
   receives: [{id, t:"HH:MM", name}, …]
   every 10 seconds:
       due = reminders where t == now "HH:MM"
       occurrenceKey = "<Date.toDateString()>|<HH:MM>|<ids>"
       if due and occurrenceKey not in fired:
           fired[occurrenceKey] = true
           beep()  ← 5 × 880 Hz sine pulses (Web Audio), 0.3s apart
       render():
           due     → class "due" (red, flashing) + "🔔 TIME TO TAKE YOUR MEDICINE — <names>"
           next    → "⏰ Next reminder: <name> at <h:mm AM/PM>"
           else    → "⏰ N reminder(s) set for today…"
   every 60 seconds: reset `fired` on date rollover
```

> These are **in-app alerts only** — no background push. The tab must stay open.

---

## 10. Documents Flow

```
file_uploader(pdf, png, jpg, jpeg, webp, accept_multiple_files=True)
   │
   for each file not already in session.documents (by name):
       data = file.getvalue()
       if len(data) > 10 MiB (MAX_DOCUMENT_BYTES) → error, continue
       file_type = file.type or "file"
       id = save_document(name, file_type, len(data))
           → INSERT documents (user_id, filename, file_type, file_path=NULL, size_bytes)
       append {id, name, file_type, size_kb, uploaded_on}

caption: "Only the file's name, type and size are stored.
          The contents themselves are neither saved nor read."

st.dataframe(Name | Type | Size (KB) | Uploaded)     ← or info if empty
```

---

## 11. History Flow

```
SELECT created_at AS timestamp, symptoms, age, severity, duration,
       predicted_urgency
  FROM triage_history
 WHERE user_id = ?
 ORDER BY created_at DESC
   │
   ├── empty → st.info("No past checks yet. Run a symptom check to see it here.")
   └── rows:
        st.dataframe(table)
        st.download_button("⬇️ Download History as CSV",
                           data=history_df.to_csv(index=False).encode("utf-8"),
                           file_name="mediscan_history.csv",
                           mime="text/csv")
```

---

## 12. Global Search Flow

```
text_input "Search"  (placeholder lists all searchable categories)
   │
   q = query.lower().strip()
   │
   ├── MEDICINES  → medicines.csv, any column contains q, head(5)
   │                 card: name · used_for · form
   ├── TRIAGE     → last 20 triage rows, any column contains q
   │                 st.dataframe
   ├── REMINDERS  → session reminders where q in name.lower() or notes.lower()
   │                 card: name · time · food
   ├── PROVIDERS  → healthcare_prices.csv, any column contains q, head(5)
   │                 dataframe[provider, city, district, procedure,
   │                            price_inr, rating]
   ├── DOCUMENTS  → session documents where q in name.lower()
   │                 card: name · uploaded date
   └── nothing    → empty state "No results found"
```

---

## 13. Profile / Notifications / Privacy / Accessibility Flows

### 13.1 Profile

```
Profile card: avatar (first letter upper) + name + username
Personal Information: name and username (disabled inputs)
Emergency Contact: st.info("<name or 'Not set'> · <phone> · <relation>")
Account:
   [Security settings] → active_feature = "Account Settings"
   [Privacy center]     → active_feature = "Privacy & Security"
   [Logout]             → logout_user() → st.rerun()
```

### 13.2 Notifications

```
notifications empty → empty state "You're all caught up"
otherwise → one .ms-notification-row per entry (message + time)
[Clear notifications] → session.notifications = [] → st.rerun()
```

### 13.3 Privacy & Security

```
3 explainer cards: Local records / Passwords (PBKDF2) / Documents (metadata only)

Manage Data
   [Clear stored documents & chat]
        DELETE FROM documents WHERE user_id = ?
        DELETE FROM chat_history WHERE user_id = ?
        session.documents = [] ; session.chat_messages = []
        st.success

Delete Account
   checkbox "I understand that deleting my account removes my saved account records"
   [Delete my account]  (disabled until checked)
        for table in [triage_history, emergency_contacts, medicine_scans,
                      reminders, documents, chat_history, sessions]:
            DELETE FROM <table> WHERE user_id = ?   (each in try/except)
        DELETE FROM users WHERE id = ?
        logout_user() → st.rerun()
```

### 13.4 Accessibility

```
st.toggle("Larger text")     → session.accessibility_large_text
st.toggle("High contrast")   → session.accessibility_high_contrast
save_persist_ui_state()      → written to sessions.ui_state so it survives reload

Applied at the very top of the logged-in run:
   large text    → <style> html,body,.stApp{font-size:17px} … </style>
   high contrast → <style> .stApp{filter:contrast(1.12)} … </style>
```

---

## 14. Sidebar Flow

```
1. Brand block      logo tile ✚ + "MediScan AI" + tagline
2. Search affordance (decorative; the real page is Search)
3. Live-status pill "AI services ready" (animated pulse + dots)
4. 13 nav buttons    (primary type + white pill when active,
                      secondary transparent when not)
5. Emergency card    🚑 Emergency / Call 108
6. ─── Account ───
      "Logged in as <name>"
      [Logout]
      Expander "Account Settings"
          caption: current username
          [Update Username] → validate [a-z0-9_]{3,30} → UPDATE users
          [Update Email]    → require "@"                  → UPDATE users
          [Update Password] → verify current password     → UPDATE users
7. ─── Language ───
      selectbox over the 12 TRANSLATIONS keys  → T = TRANSLATIONS[language]
      if not assistant_language_manual:
          assistant_language = alias(language)     # English / हिन्दी / తెలుగు
8. ─── Emergency Contact ───
      st.form (name, phone, relation)
      [Save Contact] → INSERT OR REPLACE emergency_contacts
                        session.emergency_contact updated
      if name set → emergency-box with a tel: link
      static India emergency numbers: 108, 100, 112, 1091
```

---

## 15. Data Hydration Flow (every logged-in run)

```
load_local_user_data()
   │
   if not user_id → return
   │
   Each group loaded independently via a local query() helper that
   warns on failure instead of blanking the other groups.
   │
   ├── emergency_contacts → session.emergency_contact
   ├── documents          → session.documents
   │      id, name, file_type, size_kb, uploaded_on
   │      (size_bytes / 1024, rounded; ISO timestamp → "%Y-%m-%d %H:%M")
   ├── chat_history       → session.chat_messages
   │      ORDER BY created_at ASC, id ASC   (id breaks same-second ties)
   ├── reminders          → session.reminders
   │      ORDER BY reminder_time ASC
   │      "%H:%M:%S" → datetime.time
   └── saved_medicines    → session.saved_medicines (names only)
```

---

## 16. Error & Degradation Paths

| Situation | Behaviour |
|-----------|-----------|
| Tesseract not installed | Warning with download link + `TESSERACT_PATH` instructions; scanner still works for typed names |
| Tesseract not on PATH | OCR error block, exception in `st.code`; manual search unaffected |
| `GROQ_API_KEY` missing | Assistant page shows a setup hint; all other features work |
| `groq` package missing | Warning "pip install groq"; all other features work |
| Groq call fails | "Assistant error: …" stored as the assistant bubble; chat remains usable |
| openFDA down/slow | Source dropped; result built from RxNorm/Wikipedia |
| RxNorm slow | Non-gating; collected opportunistically; often empty — no user-visible failure |
| Wikipedia returns a disambiguation page | Rejected; next candidate tried |
| openFDA score below threshold | Treated as no match; `matched=False` shown honestly |
| No source recognises the name | "No database match" card + explanation + search-anywhere links |
| `medicines.csv` missing | Error; scanner page continues with web search only |
| `healthcare_prices.csv` missing | Error with the exact expected path |
| `triage_model.pkl` missing/corrupt | `st.error` + `st.stop()` (app cannot predict without it) |
| SQLite write failure | `st.warning("Could not save your …")`; page keeps working |
| SQLite read failure (one table) | Warning naming that group; other groups still load |
| Account deleted while a session cookie lives | Restore query joins `users` → no row → logged out cleanly |
| Stale `ui_state` page name | Rejected by `PERSISTED_FEATURES` allow-list |
| File over 10 MiB | Per-file error; the rest of the batch still processes |

---

## 17. End-to-End Journeys

### Journey A — New user, first triage
```
Register → Create Account → Login → 30-day cookie set
  → Home (greeting, stats empty)
  → [Start Health Check →]
  → Triage: symptoms "chest pain since morning", age 45, duration "1 day", Severe
  → Analyze
      emergency keyword "chest pain" matches → EMERGENCY override
      confidence 100%, no model call
      INSERT triage_history
      activity + notification
  → Result: 🚨 EMERGENCY banner, safety override metric, red block,
            no probabilities, educational-prototype caption
  → Reload page → restored to Triage page, still signed in
  → History → 1 row → Download CSV
```

### Journey B — Identify an Indian brand medicine
```
Login → Medicine Scanner
  → (right column) type "Dolo 650", "Search the web" ON
  → [🔍 Identify Medicine]
      local CSV miss
      web search:
        openFDA: brand query → relevance check → "Acetaminophen" label accepted
        Wikipedia: no "Dolo 650" article → retry with generic "Acetaminophen"
        RxNorm: clinical-drug + branded-drug names
      merged: matched=True, confidence="partial" → amber banner
  → Web results: source line, image, summary, facts grid
  → "Search anywhere" links (1mg, PharmEasy, MedPlus, Google Images)
  → Switch assistant reply language to हिन्दी → lookup re-runs
  → [Explain in हिन्दी] → Groq (temperature 0.3, max 320 tokens)
     5 bullet summary, no dosage, name kept in Latin script
  → [Set Reminder] → Reminders page with "Dolo 650" pre-filled
  → [Save to Medicines] → INSERT OR IGNORE saved_medicines
```

### Journey C — Find an affordable scan nearby
```
Login → Hospital Finder
  → City: Hyderabad
  → Revenue District: (multiple available)
  → District/Mandal: chosen mandal
  → Procedure search: "MRI" → select MRI Brain
  → Best hospital card (highest rating, cheapest tie-break)
  → Price summary: lowest / average / highest in-district
  → In-district table with ratings, prices, tel: and Maps links
  → Out-of-district suggestions in the same revenue district
  → Bar chart (price) + scatter (rating vs price)
  → Potential savings: ₹X (Y%) vs the highest-priced provider
  → Sample-data caption
```

### Journey D — Multilingual assistant
```
Login → sidebar Language: తెలుగు
  → assistant_language = తెలుగు (follows the sidebar)
  → AI Assistant
  → one-tap chip: "Dolo 650 మందిని భోజనం ముందు తీసుకోవాలా లేదా తర్వాత?"
  → Groq system prompt: English safety rules + Telugu instruction
     + "closing reminder in the same language" + "under 150 words"
  → Telugu answer, brand name in Latin script, doctor reminder included
  → [Translate last answer] → switch to English → answer re-rendered in English
  → chat_history updated in place (UPDATE, not INSERT)
  → Reload → chat restored from SQLite, language flag persisted
```

### Journey E — Account deletion
```
Sidebar → Profile → Privacy & Security
  → review the three explainer cards
  → [Clear stored documents & chat]      (optional)
  → tick "I understand that deleting my account…"
  → [Delete my account] becomes enabled
  → DELETE from triage_history, emergency_contacts, medicine_scans,
            reminders, documents, chat_history, sessions  (per user)
  → DELETE from users
  → logout_user() → cookie cleared, session wiped, st.rerun()
  → login screen
```
