# Design Specification — MediScan AI

Covers the visual language, component library, interaction patterns, accessibility, and the full token set that drives the interface.

---

## 1. Design Principles

| # | Principle | How it shows up in the code |
|---|-----------|----------------------------|
| 1 | **Calm clinical trust** | Teal palette, generous whitespace, soft shadows — reads as clinical, not consumer-tech |
| 2 | **One visual system everywhere** | Auth pages and the logged-in dashboard share the exact same tokens; `load_css()` runs *before* the auth gate for this reason |
| 3 | **Safety is always visible** | Red/amber banners, disclaimers under every AI output, "not a diagnosis" captions, persistent emergency contacts |
| 4 | **Progressive disclosure** | Simple question → short answer; a detail grid and source links only for those who scroll |
| 5 | **Degrade, never break** | Optional integrations disappear gracefully; every failure has a human-readable message and a next action |
| 6 | **Respect the user's system** | `prefers-color-scheme` dark mode, `prefers-reduced-motion`, visible focus rings |
| 7 | **Bilingual by design** | UI is localised to 12 languages; the AI layer is localised to 3 with a shared English safety core |
| 8 | **Keyboard-first** | Real `<button>`/`<input>` elements via Streamlit widgets; custom HTML is decorative only |

---

## 2. Design Tokens

All tokens live in `:root` in `assets/style.css`. No hard-coded colours outside the token block (except intentional semantic colours: red emergency, amber "due", green "low urgency").

### 2.1 Colour — Light Theme

| Token | Value | Usage |
|-------|-------|-------|
| `--primary` | `#0b7a75` | Primary actions, active nav, links |
| `--primary-dark` | `#075e5a` | Gradient start, hover states |
| `--accent` | `#19b5a5` | Highlights, focus rings, eyebrows, "selected" chips |
| `--accent-soft` | `rgba(25,181,165,.14)` | Tinted surfaces, AI user bubbles, live pills |
| `--danger` | `#d64545` | Errors, emergency, "High" urgency |
| `--ink` | `#0e2a2b` | Headings, strong text, values |
| `--text` | `#16333a` | Body copy, table cells |
| `--text-secondary` | `#4d6b74` | Labels, subtitles, secondary values |
| `--text-muted` | `#7c949c` | Captions, hints, timestamps, placeholders |
| `--border` | `#d9ece9` | Every card/input/table edge |
| `--card` | `#ffffff` | Card, input, table, modal surfaces |
| `--card-soft` | `#f2faf9` | Nested tiles, uploader dropzone, pills |
| `--shadow` | `0 8px 26px rgba(16,42,43,.08)` | Cards, metrics, tables |
| `--shadow-lg` | `0 18px 45px rgba(5,75,73,.16)` | Hero, featured medicine/web cards |

### 2.2 Colour — Dark Theme

Triggered by `@media (prefers-color-scheme: dark)`. Semantic role is preserved; only the values change.

| Token | Dark value |
|-------|-----------|
| `--primary` | `#2dd4bf` |
| `--primary-dark` | `#0b7a75` |
| `--accent` | `#5eead4` |
| `--accent-soft` | `rgba(45,212,191,.16)` |
| `--danger` | `#f87171` |
| `--ink` | `#e9f4f4` |
| `--text` | `#d3e4e7` |
| `--text-secondary` | `#9db6bd` |
| `--text-muted` | `#7c949c` |
| `--border` | `#27434c` |
| `--card` | `#10232c` |
| `--card-soft` | `#0c1c24` |
| `--shadow` | `0 10px 30px rgba(0,0,0,.40)` |
| `--shadow-lg` | `0 20px 50px rgba(0,0,0,.50)` |

**Semantic exceptions (both themes):**

| Element | Light | Dark |
|---------|-------|------|
| Emergency box | `#fff5f5` bg / `#f1b7bd` border / `#b4232f` link | `#3a1719` / `#7a2d34` / `#ff9b9b` |
| Reminder "due" card | `#fff8ef` bg / `#f0a35a` border | `#3a2a12` / `#8a5a2b` |
| Best-hospital card border | `#bde5df` | `#2a4a4e` |
| Fuzzy-match warning | `#fff6e5` bg / `#f0b357` border / `#8a5a00` text | — |

### 2.3 App Background

```css
--app-bg: linear-gradient(rgba(244,251,250,.96), rgba(247,252,251,.98)),
          url(<unsplash medical photo>) center/cover fixed no-repeat;
```

A high-opacity gradient over a medical photograph. The `fixed` attachment keeps the image still while the page scrolls — the perceived "depth" stays constant.

### 2.4 Sidebar

The sidebar is intentionally **inverted** (dark) against a light app:

```css
background: linear-gradient(180deg, #053436 0%, #07585a 55%, #0a6f6c 100%);
border-right: 1px solid rgba(255,255,255,.12);
```

All text forced to `#effaf9`. Inputs use translucent white (`rgba(255,255,255,.12)`) with a dark popover (`#0a4f4d`).

### 2.5 Typography

| Role | Family | Size | Weight | Letter-spacing |
|------|--------|------|--------|-----------------|
| H1 (hero) | Inter | `clamp(24px, 3vw, 34px)` | 800 | −0.5px |
| H2 (page) | Inter | `clamp(20px, 2.5vw, 27px)` | 800 | −0.3px |
| H3 (section) | Inter | `clamp(17px, 2vw, 20px)` | 800 | −0.3px |
| Body | Inter | 14.5px / 1.62 | 400 | — |
| Input label | Inter | 13px | 600 | — |
| Caption | Inter | 12.5px | 400 | — |
| Eyebrow (kicker) | Inter | 11px | 800 | +2.5px, uppercase |
| Stat value | Inter | `clamp(20px, 2.4vw, 26px)` | 800 | — |
| Metric value | Inter | 24px | 800 | — |
| Message label | Inter | 11px | 800 | +0.8px, uppercase |
| Grid tile label | Inter | 11px | 700 | +1.1px, uppercase |

Font stack: `'Inter', -apple-system, 'Segoe UI', Roboto, Arial, sans-serif`, loaded from Google Fonts via `@import`.

**Explicit override:** Inter is forced on Streamlit's own markdown headings (which default to "Source Sans") through a targeted selector list covering `.stApp`, every `.stMarkdown` heading/paragraph/list/link/strong/em, every widget label, and Streamlit internals.

### 2.6 Spacing, Radius, Elevation

| Token | Value | Applied to |
|-------|-------|-----------|
| `--radius` | `14px` | Inputs, tiles, metrics, small cards |
| `--radius-lg` | `20px` | Feature cards, stat cards, hero, web/medicine results, empty states |
| Base content | `max-width: 1240px` | `.block-container` |
| Page padding | `1.4rem` top / `3rem` bottom | `.block-container` |
| Card padding | `13–22px` | Scales with card size |
| Grid gap | `12–16px` | Card grids, tab lists |
| Section gap | `30px` | `.ms-home-section-gap` |
| Row gap | `9–14px` | Activity rows, notifications, search results, web links |

### 2.7 Buttons

```css
background: linear-gradient(135deg, var(--primary-dark), var(--accent));
color: #ffffff;
border: 0; border-radius: 11px;
font-weight: 700; font-size: 14px; min-height: 42px;
box-shadow: 0 7px 18px rgba(11,122,117,.20);
```

| State | Transform | Shadow |
|-------|-----------|--------|
| Default | — | `0 7px 18px rgba(11,122,117,.20)` |
| Hover | `translateY(-2px)` | `0 11px 26px rgba(11,122,117,.30)` |
| Active | `translateY(0)` | — |
| Disabled | `opacity .55`, `filter: saturate(.6)` | none |

**Sidebar buttons are deliberately quiet** — transparent background, no shadow, left-aligned text, 40px min-height — so the nav does not compete with page content. The *active* item inverts to a solid white pill with `#0b5c58` text.

### 2.8 Inputs

```css
border-radius: 12px;
border: 1px solid var(--border);
background: var(--card);
color: var(--ink);
font-size: 14px;
caret-color: var(--primary);
```

**Focus:** `border-color: var(--accent)` + `box-shadow: 0 0 0 3px var(--accent-soft)` — a three-layer ring (border, glow, offset) that stays visible in both themes.

**Auth-specific:** on login/register the first input's top corners are squared (`0 0 12px 12px`) so the inputs visually join the card header above them. The whole form column is constrained to `max-width: 430px` and centred via a `:has()` selector keyed on `.auth-logo-center`.

### 2.9 Focus & Motion

```css
:focus-visible {
  outline: 3px solid var(--accent) !important;
  outline-offset: 2px;
  border-radius: 8px;
}
```

Applied to links, buttons, inputs, textareas, selects, and any `[tabindex]` element.

`@media (prefers-reduced-motion: reduce)` collapses **all** animation and transition durations to `0.001s` and iterations to `1`.

---

## 3. Animation Inventory

| Keyframe | Target | Duration | Easing | Purpose |
|----------|--------|----------|--------|---------|
| `msSpin` | `.ms-spinner`, `.ms-button-spinner` | 0.8s / 0.7s | linear ∞ | Basic rotation |
| `msPing` | `.ms-status-pulse::after` | 1.6s | ease-out ∞ | "Live" halo |
| `msDot` | `.ms-status-dots i` | 1.2s | ease-in-out ∞ | Typing/ready indicator (staggered 0s / .15s / .3s) |
| `msDotBig` | `.ms-dots span` | 1.1s | ease-in-out ∞ | Loading dots (same stagger) |
| `msPulseGlow` | `.ms-pulse`, `.ms-pulse-icon` | 1.4s / 2.4s | ease-in-out ∞ | Tip-icon glow |
| `msflash` | Reminder strip `.due` | 1s | ∞ | Due-now flash (opacity 1 → .55) |
| `fadeIn` | Page enters, AI welcome | 0.3–0.6s | ease | Entrance |
| `fadeInDown` | `.main-header` | 0.5s | ease | Header slide-down |

**Transform-only motion rule:** hover effects translate, never repaint. Cards lift `4px`, list rows slide `3px` on the x-axis — small, directional, and reversible.

---

## 4. Component Library

Every component is a raw-HTML block injected with `st.markdown(..., unsafe_allow_html=True)`, styled by a `ms-` prefixed class. All interpolated content passes through `safe_text()`.

### 4.1 Auth Shell

| Class | Purpose |
|-------|---------|
| `.auth-logo-center` | Centred logo, `max-height: 60px`, drop-shadow |
| `.auth-card-top` | Card header, `max-width: 430px`, 3px primary top border, `--radius` top corners |
| `.auth-card-kicker` | "WELCOME BACK" / "GET STARTED" eyebrow |
| `.auth-switch-label` | "Don't have an account?" divider |
| `.auth-forgot-label` | Right-aligned "Forgot your password?" link |
| `.auth-security-note` | Shield ✓ + "Your information stays within your MediScan AI account." |
| `.auth-footer` | Not-a-substitute-for-advice disclaimer |

The `:has()` selector `[data-testid="stMain"]:has(.auth-logo-center) …` scopes the narrow centred layout to the auth pages only — logged-in pages are unaffected.

### 4.2 Branding & Header

| Class | Purpose |
|-------|---------|
| `.ms-sidebar-brand` / `.ms-sidebar-logo` | ✚ tile + wordmark + tagline |
| `.ms-sidebar-search` | Decorative search affordance |
| `.ms-live-status` | "AI services ready" pill with pulse + dots |
| `.ms-app-logo-row` / `.ms-app-logo` | Branded logo card, top-right of every logged-in page (in normal flow, not fixed, so it scrolls naturally) |
| `.main-header` | Full-width teal banner with background photo, `--radius-lg: 22px`, 30/34px padding |
| `.page-location` | "Home / **Feature**" breadcrumb |

### 4.3 Home Dashboard

| Class | Purpose |
|-------|---------|
| `.ms-dashboard-welcome` | Greeting row: copy left, live pill right |
| `.ms-eyebrow` | 11px uppercase letter-spaced kicker |
| `.ms-home-hero` | 300px dark-teal gradient panel; `overflow: hidden` |
| `.ms-home-hero-copy` / `h1 span` | Headline with mint-highlighted second line |
| `.ms-home-doctor` | Absolutely-positioned doctor image, `background-size: contain`, bottom-right, `pointer-events: none` |
| `.ms-home-trust` | Glassmorphic "✓ Trusted" badge (backdrop blur, 12% white, 28% border) |
| `.ms-home-feature` (+ `.blue/.coral/.cyan/.green/.amber/.violet`) | Action card; the tone class only tints the 46px icon tile |
| `.ms-stat-card` (+ tone) | Metric tile; tone colours icon, label, and value |
| `.ms-activity-row` | 38px icon tile + title/detail + right-aligned timestamp |
| `.ms-empty-state` | Dashed 1.5px border, 60px circular icon, title + hint |
| `.ms-health-tip` | Mint gradient band, 44px pulsing tip icon, right arrow |
| `.ms-section-title` / `.ms-home-section-gap` | Section heading + 30px top margin |

**Hero image injection:** `__MEDISCAN_HERO_DOCTOR__` in the CSS is replaced at runtime by a base64 data URL built from `assets/hero_doctor_reference.png` inside `load_css()`. No extra network request, no broken image if the file is missing.

### 4.4 Result Cards

| Class | Purpose |
|-------|---------|
| `.ms-medicine-result` | Local demo-CSV match (shadow-lg) |
| `.ms-web-result` | Merged web lookup (shadow-lg) |
| `.ms-medicine-title` | 46px gradient "M"/"?" tile + uppercase source label + `clamp(19–24px)` name + optional image |
| `.ms-medicine-grid` | `repeat(auto-fit, minmax(220px, 1fr))` fact tiles |
| `.ms-medicine-grid > div` | `--card-soft` tile, 11px uppercase label + 13.5px value |
| `.ms-web-image` | 74px contain-fitted thumbnail |
| `.ms-web-summary` | Wikipedia extract in a soft tile |
| `.ms-web-note` / `.ms-web-note-warn` | Plain note / amber "closest match, not exact" |
| `.ms-web-link` | Full-width link row: label left, hint right, slides 3px on hover |

### 4.5 AI Chat

| Class | Purpose |
|-------|---------|
| `.ai-page-header` / `.ai-icon` | 54px gradient tile (radius 17px) + title + subtitle |
| `.ai-disclaimer` | Mint pill, 12.5px, sits directly under the header |
| `.ai-welcome-card` | Centred 64px icon, headline, body, chips, ready row |
| `.ai-suggestions span` | Pill chips (`--card-soft`, radius 999px) |
| `.ai-user-message` | `accent-soft` bg + accent border, `margin-left: 12%` |
| `.ai-assistant-message` | `--card` bg + border, `margin-right: 12%` |
| `.ai-message-label` | 11px uppercase role label |
| `.ai-ready-animation` | "Ready to help" + animated dots |

The 12% inset on each side is the entire visual language of the chat: a consistent left/right gutter that breaks down to 4% under 768px.

### 4.6 Data & Utility

| Class | Purpose |
|-------|---------|
| `.ms-profile-card` | 58px gradient avatar (first letter) + name + username |
| `.ms-notification-row` | Message left, timestamp right |
| `.ms-privacy-grid` / `.ms-privacy-card` | `auto-fit minmax(220px,1fr)` explainer cards |
| `.ms-search-result` | Result row: bold title + secondary detail |
| `.best-hospital-card` | Provider + district, rating, price, tel:, Maps link |
| `.reminder-card` / `.reminder-due` | Schedule card; amber variant when within ±30 min |
| `.emergency-box` | Tinted red box; tel: links in `--danger` |
| `.page-location` | Breadcrumb |
| `.ms-page-enter` / `.ms-page-subtitle` | Page intro block |

### 4.7 Loading States

| Class | Purpose |
|-------|---------|
| `.ms-loading-overlay` | `position: fixed; inset: 0; z-index: 999` full-screen blocker |
| `.ms-loading-content` | Column flex: spinner + 13.5px message |
| `.ms-spinner` | 34px circle, 4px border, rotating `border-top-color` |
| `.ms-processing-button` | Gradient pill with inline spinner + "Processing..." |
| `.ms-button-spinner` | 15px circle, white on 35% white border |
| `.ms-dots span` | Three 10px accent dots, staggered bounce |
| `.ms-pulse` | 44px ring with `msPulseGlow` |
| `.ms-inline-loader` | Inline card (26px padding) for OCR / AI waits |
| `.ms-inline-loader-small` | 18px padding variant |
| `.ms-ai-loader` | 14px vertical margin variant |
| `.ms-spinner-page` / `.ms-spinner-grid` / `.ms-spinner-card` | Design-system gallery page |
| `.ms-spinner-overlay-demo` | Isolated overlay preview |

**Spinner decision rule:**

| Need | Component |
|------|-----------|
| Short, local, uninterruptible | `st.spinner()` |
| Inline replaceable block | `st.empty()` + `.ms-inline-loader` |
| Global, blocking, branded | `.ms-loading-overlay` |
| Reminder strip countdown | Client iframe (own timer) |

---

## 5. Layout

### 5.1 Shell

```
┌──────────────┬────────────────────────────────────────────────┐
│              │  [logo card, right-aligned]                   │
│  SIDEBAR     │  ┌── main header banner (non-Home) ────────┐  │
│  240px       │  │ MediScan AI                          │  │
│              │  │ Healthcare Price Transparency & Triage│  │
│  brand       │  └───────────────────────────────────────┘  │
│  search      │  [reminder strip — only when reminders exist] │
│  live pill   │  ← Back | Home / Feature                    │
│  nav × 13    │  ┌── page content (max-width 1240px) ────┐   │
│  emergency   │  │                                      │   │
│  ── Account  │  │                                      │   │
│  ── Language │  │                                      │   │
│  ── Contact  │  └──────────────────────────────────────┘   │
└──────────────┴────────────────────────────────────────────────┘
```

### 5.2 Page Templates

| Template | Used by |
|----------|---------|
| **Dashboard** | Home only — hero, 3-col action grid, 3-col stats, activity list, tip band |
| **Header + content** | Triage, Hospital Finder, Medicine Scanner, Documents, History — banner + free-flow body |
| **Intro + content** | Search, Profile, Notifications, Privacy, Accessibility, Loading Spinners — `.ms-page-enter` (h2 + subtitle) then body |
| **Chat** | AI Assistant — header, disclaimer, controls, message stream, chat input |
| **Form + list** | Reminders — header, caption, add form, due-sorted card list |
| **Two-column** | Medicine Scanner — camera column \| manual column, then full-width results below |

---

## 6. Responsive Behaviour

| Breakpoint | Changes |
|------------|---------|
| **> 900px** | Full layout; doctor image `min(300px, 38%)`; privacy grid auto-fit |
| **≤ 900px** | Doctor image `min(230px, 34%)` at 0.8 opacity; hero min-height 280px, padding 28/24px; privacy grid single column |
| **≤ 768px** | Content padding `.9rem .8rem 2rem`; header padding 24/20px, radius 18px, h1 26px; **hero becomes a vertical stack** (flex-column, justify-end, min-height 330px) with the doctor image at 52% width / 0.5 opacity anchored right; trust badge moves from bottom-left to top-left; medicine grid single column; web links stack vertically; chat gutters shrink to 4%; tab lists scroll horizontally with 12px labels |

Both hero breakpoints keep the copy above the image and the badge inside the panel (`overflow: hidden`).

---

## 7. Accessibility

### 7.1 Built-in Support

| Feature | Mechanism |
|---------|-----------|
| Larger text | Accessibility toggle → inline `<style>`: `html, body, .stApp { font-size: 17px }` plus 1.05rem on markdown, inputs, selects, textareas. Persisted in `sessions.ui_state` |
| High contrast | Accessibility toggle → `.stApp { filter: contrast(1.12) }` + 2px borders on stat cards, activity rows, search results, notifications. Persisted the same way |
| Visible focus | 3px `--accent` outline with 2px offset on every interactive element |
| Reduced motion | All durations → 0.001s, all iterations → 1 |
| Semantic HTML | Real `button`/`input`/`select` via Streamlit widgets; no clickable `div`s |
| Labels | Every input has a visible `label`; placeholders are hints, not labels |
| Live regions | `st.toast` and `st.info` announce state changes |
| Keyboard nav | All 13 nav items, all forms, and the chat input are tab-reachable |
| Language | 12-language UI, 3-language assistant — removes language as a barrier |
| Contrast | Body `#16333a` on `#ffffff` ≈ 12:1; muted `#7c949c` on white ≈ 3.6:1 (captions/hints only) |

### 7.2 Known Gaps

- Decorative HTML blocks (`.ms-activity-row`, `.ms-home-feature`) are not focusable — every one of them has a real Streamlit button directly below it, so the action remains keyboard-reachable.
- ARIA roles/labels are not set on custom cards.
- No full WCAG audit has been performed.

---

## 8. Internationalisation Design

### 8.1 Two Independent Language Layers

| Layer | Coverage | Mechanism |
|-------|----------|-----------|
| **App chrome** | 12 languages | `TRANSLATIONS` dict: `st.selectbox` in sidebar, `T = TRANSLATIONS[language]` |
| **AI replies** | 3 (English, हिन्दी, తెలుగు) | `ASSISTANT_LANGUAGES` dict + `build_assistant_prompt()` |

`ASSISTANT_LANGUAGE_ALIASES` maps the sidebar language onto an assistant language so the two follow each other by default:

```
"Hindi"  → "हिन्दी"
"Telugu" → "తెలుగు"
"English"→ "English"
```

The assistant then becomes independently switchable. `assistant_language_manual` tracks whether the user has taken control; once they have, the sidebar no longer overrides their choice.

### 8.2 Key Design Decisions

**Safety rules stay English.** Only the *output language* instruction is translated. LLMs follow English safety constraints more reliably than translated ones, and the rules are not user-facing.

**Medicine names stay in Latin script.** Every non-English instruction says so explicitly. Translating "Dolo 650" or "Pantoprazole" makes it unsearchable and risks pointing a user at a different product.

**The switch is labelled in the current language.** The reply-language selectbox uses `format_func=lambda name: ASSISTANT_LANGUAGES[name]["native"]`, so a user already in Telugu is not forced to read English to switch back.

**Suggestions are written in the reply language**, not just the UI language — a user who cannot read English still has one-tap starting points.

**Data content is not translated.** Procedure names, hospital names, CSV fields, and free-text symptom input stay exactly as they are. The `TRANSLATIONS` dict covers app labels only.

**Known caveat:** the 12-language `TRANSLATIONS` strings are machine-translated and the code comments flag that a native speaker should review them before a real submission or demo.

---

## 9. Content & Voice

### 9.1 Tone

| Do | Don't |
|----|-------|
| "Predicted Urgency: EMERGENCY" | "You might have…" |
| "This is general information, not medical advice." | "You should definitely take…" |
| "Couldn't confidently match this photo. Try the manual search instead." | "Invalid input." |
| "Couldn't load your reminders: …" | "Error 500" |
| "The Tesseract OCR program isn't installed" | "OCR failed" |

Principles: **plain English, no jargon, honest about uncertainty, always state limits.**

### 9.2 Disclaimers (present in the product)

| Location | Text |
|----------|------|
| Auth footer | "MediScan AI provides general health information and is not a substitute for professional medical advice." |
| Auth security note | "Your information stays within your MediScan AI account." |
| Non-Home header | "MediScan AI is an educational prototype and does not provide medical diagnosis or replace professional medical advice." |
| Triage result | "⚠️ This is an educational AI prototype and is not a medical diagnosis." |
| Triage emergency block | "Emergency symptoms were detected. Please seek immediate medical attention from a qualified healthcare professional." |
| Diet suggestion | `DIET_DISCLAIMER` — "general wellness information only, not a personalized medical or nutrition plan." |
| Medicine scanner | "Follow your prescription or pharmacist's instructions. This scanner does not determine a personal dosage." |
| Medicine web result | "Reference information from openFDA… never includes a dosage for you to follow." |
| AI Assistant | Per-language `disclaimer` string under the header |
| Hospital Finder | Prices/ratings/phones are sample data; map links are searches, not coordinates |
| Documents | "Only the file's name, type and size are stored. The contents themselves are neither saved nor read by this app." |
| Privacy | "For a real deployment, review your privacy policy, retention rules, and healthcare-data requirements with a qualified professional." |
| Reminders | "It is an in-app alert, not a background push notification." |
| Sidebar | 108 · 100 · 112 · 1091 |

### 9.3 Iconography

Emoji only — 🩺 💊 🤖 🏥 ⏰ 📋 📁 👤 🔔 🔐 ♿ 🔍 📷 📊 ⭐ 💰 📈 💡 📄 🚑 ✚ 🔔 ⚠️ ✅ ❌. Rendered identically on Windows/macOS/Linux with no icon-font dependency; paired with a text label or `aria` context so meaning never depends on the glyph alone.

---

## 10. Safety Design

| Risk | Design Control |
|------|----------------|
| Missing an emergency | 14-keyword pre-check runs **before** the model and forces `Emergency` + a red banner + a "Safety Override" metric + explicit seek-care instruction |
| Model overconfidence | Confidence % and per-class probability bars are always shown — never a bare label |
| Over-relying on AI | Three-way messaging: "educational prototype", "not a diagnosis", "consult a doctor/pharmacist" |
| Dosage harm | openFDA's `dosage_and_administration` is never parsed; the LLM prompt explicitly forbids dose/tablet-count/strength; the scanner card says it "does not determine a personal dosage" |
| LLM hallucination | Assistant is instructed to say plainly when it is unsure rather than guess; medicine facts are grounded in merged public-database data before the LLM is asked only to explain it |
| Wrong medicine match | Whole-word relevance scoring, weighted name scoring, "closest match" amber banner, and an honesty prompt when nothing is recognised |
| Health-data exposure | No third-party analytics; document contents never stored; tokens stored only as digests |
| Account enumeration | Uniform reset flow; identical response for known and unknown emails |
| XSS | `safe_text()` = `html.escape(..., quote=True)` on every interpolated value; `rel="noopener noreferrer"` on external links |

---

## 11. Performance Design

| Technique | Detail |
|-----------|--------|
| Token-driven CSS | One palette swap re-themes the whole app; no per-component colour values |
| Tokenised images | Logo and hero inlined as base64 data URLs at runtime — zero extra HTTP requests |
| Deferred CSS | `load_css()` runs before the auth gate so login and app share one system with no flash of unstyled content |
| Concurrent lookups | openFDA + Wikipedia + RxNorm in a 3-worker pool |
| Deadline-bounded | 3.0s per request, 5.5s per stage, 3.0s Wikipedia retry — a slow source can never block the page |
| Opportunistic sources | RxNorm is collected non-blocking; it only adds alternative names |
| LRU cache | `@lru_cache(maxsize=256)` on the merged lookup — repeat searches are instant and public APIs are not hammered |
| Transform-only motion | Hover effects translate only; no layout-triggering properties |
| Lazy rendering | Result blocks render only when the corresponding session key exists |
| Column-indexed keys | Streamlit container keys (`.st-key-hero_cta_start button`) target individual buttons without touching global button styles |
| Scope selectors | `:has()` confines the narrow auth layout; media queries keep the layout narrow-only on small screens |

---

## 12. Design Debt

| Item | Impact | Suggested Fix |
|------|--------|---------------|
| `app.py` renders HTML strings inline (≈100 blocks) | Hard to review, diff, or unit-test | Extract to a `components/` package of render functions |
| Hard-coded semantic colours outside tokens | Dark-mode overrides must be maintained by hand | Add `--emergency-bg`, `--warn-bg`, `--success-bg` tokens |
| Decorative cards are not focusable | Keyboard users get the button, not the card | Add `tabindex` + `role`, or drop the hover affordance on non-interactive cards |
| No ARIA labels on custom components | Screen readers read bare text | Add `aria-label` / `aria-live` to the key custom blocks |
| The Telugu "summer" string uses a `.replace()` hack | Fragile, easy to break | Fix the source string |
| `modules/database.py` is dead code | Confuses contributors | Delete or move to `legacy/` |
| Tab styling retained in CSS but no tabs are used | Dead CSS | Remove the tab block |
| `medicine_scans` table exists but is never written to | Feature is half-built | Wire up scan logging or remove the table |
| Machine-translated UI strings | Quality risk | Native-speaker review pass |
| No component storybook | Designers cannot preview states | The Loading Spinners page is a partial substitute — formalise it |
