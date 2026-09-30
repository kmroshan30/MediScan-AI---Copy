import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import joblib
import re
import os
import json
import sqlite3
import hashlib
import difflib
import datetime
import base64
import binascii
import hmac
import secrets
import time
from pathlib import Path
from html import escape
from urllib.parse import quote

from database.database import init_db, get_connection
from modules.medicine_search import search_medicine

# ============================================================
# MEDISCAN LOADING SPINNERS
# ============================================================

def show_loading_spinner(message="Loading content..."):
    """Show a lightweight MediScan loading overlay."""
    return st.markdown(
        f"""
        <div class="ms-loading-overlay">
            <div class="ms-loading-content">
                <div class="ms-spinner"></div>
                <p>{message}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def show_processing_button():
    """Visual processing button matching the requested spinner style."""
    return st.markdown(
        """
        <div class="ms-processing-button">
            <span class="ms-button-spinner"></span>
            <span>Processing...</span>
        </div>
        """,
        unsafe_allow_html=True
    )


# ============================================================
# Optional imports (features degrade gracefully if missing)
# ============================================================

try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

try:
    from groq import Groq
    GROQ_LIB_AVAILABLE = True
except ImportError:
    GROQ_LIB_AVAILABLE = False

# ============================================================
# Page Configuration (must be the first Streamlit command)
# ============================================================

st.set_page_config(
    page_title="MediScan AI",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
MODEL_PATH = BASE_DIR / "models" / "triage_model.pkl"
PRICE_DATA_PATH = BASE_DIR / "data" / "healthcare_prices.csv"
MEDICINE_DATA_PATH = BASE_DIR / "data" / "medicines.csv"
CSS_PATH = BASE_DIR / "assets" / "style.css"
LOGO_PATH = BASE_DIR / "assets" / "mediscan_logo.png"

# Initialize local SQLite database
init_db()

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024

PASSWORD_HASH_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    """Hash stored passwords with a unique salt and PBKDF2-HMAC-SHA256."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_HASH_ITERATIONS
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PASSWORD_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify new hashes and transparently accept legacy SHA-256 hashes."""
    value = str(encoded or "")
    if value.startswith("pbkdf2_sha256$"):
        try:
            algorithm, iterations, salt_text, digest_text = value.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
            expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                int(iterations),
            )
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError, binascii.Error):
            return False
    # One-time compatibility for databases created by older versions.
    return hmac.compare_digest(
        hashlib.sha256(password.encode("utf-8")).hexdigest(), value
    )


def _local_reset_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_local_reset_token(email: str) -> str:
    """Issue a short-lived, one-use token for the development database.

    The caller always receives a token-shaped value, even when the email is
    unknown, so the flow does not disclose account existence.  In a real
    deployment this token would be delivered by an email provider instead.
    """
    normalized = str(email or "").strip().lower()
    raw_token = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        user = conn.execute(
            "SELECT id FROM users WHERE lower(email) = ?", (normalized,)
        ).fetchone()
        if user:
            conn.execute(
                "UPDATE password_reset_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                (int(time.time()), int(user["id"])),
            )
            conn.execute(
                """
                INSERT INTO password_reset_tokens (token_hash, user_id, expires_at)
                VALUES (?, ?, ?)
                """,
                (
                    _local_reset_token_hash(raw_token),
                    int(user["id"]),
                    int(time.time()) + 15 * 60,
                ),
            )
            conn.commit()
    finally:
        conn.close()
    return raw_token


def consume_local_reset_token(email: str, token: str) -> int | None:
    normalized = str(email or "").strip().lower()
    token_hash = _local_reset_token_hash(str(token or ""))
    now = int(time.time())
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT t.user_id
            FROM password_reset_tokens t
            JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ?
              AND lower(u.email) = ?
              AND t.used_at IS NULL
              AND t.expires_at > ?
            """,
            (token_hash, normalized, now),
        ).fetchone()
        if not row:
            return None
        user_id = int(row["user_id"])
        updated = conn.execute(
            """
            UPDATE password_reset_tokens
               SET used_at = ?
             WHERE token_hash = ? AND used_at IS NULL
            """,
            (now, token_hash),
        )
        conn.commit()
        return user_id if updated.rowcount == 1 else None
    finally:
        conn.close()


def logo_data_url():
    """Return the bundled MediScan logo as a browser-safe data URL."""
    if not LOGO_PATH.exists():
        return ""
    import base64
    return "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")

# ============================================================
# Load external stylesheet before authentication so the login page
# uses the same visual system as the logged-in application.
# ============================================================
def load_css(path: Path):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            css = f.read()
        hero_img = path.parent / "hero_doctor_reference.png"
        if hero_img.exists():
            import base64
            encoded = base64.b64encode(hero_img.read_bytes()).decode("ascii")
            css = css.replace("__MEDISCAN_HERO_DOCTOR__", f"data:image/png;base64,{encoded}")
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    else:
        st.warning("Stylesheet not found at assets/style.css — using default look.")

load_css(CSS_PATH)

# ============================================================
# Optional Windows Tesseract path
# ------------------------------------------------------------
# On Windows, Tesseract OCR is a separate program (not a pip
# package) and often isn't on PATH. If pytesseract can't find
# it automatically, set the TESSERACT_PATH environment variable.
# Download: https://github.com/UB-Mannheim/tesseract/wiki
# ============================================================

if OCR_AVAILABLE:
    tesseract_path = os.environ.get("TESSERACT_PATH")
    if tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = tesseract_path

# ============================================================
# Groq API key (for the AI Assistant tab)
# ------------------------------------------------------------
# Set this either as an environment variable GROQ_API_KEY, or
# in a local file .streamlit/secrets.toml as:
#   GROQ_API_KEY = "your_key_here"
# ============================================================

GROQ_KEY_SOURCE = None
GROQ_KEY_PROBLEM = None


def _read_groq_key():
    """Find the Groq key, and say where it came from when there isn't one.

    Order matters: an environment variable wins, so a deploy can override a
    stale secrets file. The key is looked up both at the top level and under a
    `[default]` table, because both are valid shapes for a secrets.toml and
    picking the wrong one is an easy mistake that looks exactly like a missing
    key. When nothing is found the reason is recorded instead of being
    swallowed, so the assistant screen can explain itself instead of only
    saying "no key".
    """
    env_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if env_key:
        return env_key, "environment variable GROQ_API_KEY", None

    try:
        secrets_obj = st.secrets
    except Exception as exc:
        return None, None, f"st.secrets is unavailable ({exc})"

    for label, getter in (
        ("secrets.toml GROQ_API_KEY", lambda: secrets_obj["GROQ_API_KEY"]),
        ("secrets.toml [default] GROQ_API_KEY", lambda: secrets_obj["default"]["GROQ_API_KEY"]),
    ):
        try:
            value = (getter() or "").strip()
        except (KeyError, AttributeError):
            continue
        except Exception as exc:
            return None, None, f"could not read {label} ({exc})"
        if value:
            return value, label, None

    return None, None, (
        "GROQ_API_KEY is not set in the environment or in "
        ".streamlit/secrets.toml"
    )


GROQ_API_KEY, GROQ_KEY_SOURCE, GROQ_KEY_PROBLEM = _read_groq_key()

# Shown on the assistant screen. A deployed app otherwise gives no way to tell
# which build is actually running, which is the difference between "the code is
# wrong" and "the old build is still being served". Bump this on every deploy.
APP_BUILD = "2026-09-30-assistant-diagnosis"

GROQ_MODEL = "llama-3.3-70b-versatile"  # verify against Groq's current model list

# gpt-oss is a *reasoning* model: it spends part of max_tokens on a hidden
# "reasoning" field before it writes the visible answer. With the default
# effort it regularly used 200-400 of our 400 tokens on reasoning alone and
# returned finish_reason="length" with content == "" — an empty reply that the
# chat then dropped silently. Pinning effort to "low" keeps reasoning to ~5
# tokens and the answer always lands inside the budget.
GROQ_REASONING_EFFORT = "low"

# `reasoning_effort` is accepted *only* by Groq's open-weight reasoning models.
# Sent to a normal chat model it is a hard 400, which used to mean the
# assistant could never answer at all once GROQ_MODEL was pointed at, say,
# llama-3.3-70b-versatile. The parameter is therefore only attached for models
# in this list, and is dropped automatically if the API rejects it anyway.
GROQ_REASONING_MODELS = frozenset()

# Streamlit Community Cloud has generous but finite CPU; a hung socket must
# fail into the retry loop instead of freezing the page for minutes.
GROQ_TIMEOUT_SECONDS = 60

# Shown when the model still returns nothing usable, so the user is never
# left staring at a question with no response and no explanation.
EMPTY_REPLY_MESSAGE = (
    "Sorry, I could not generate a reply just now. Please try asking again."
)


def _is_unsupported_param_error(exc):
    """Whether the API refused a request parameter this app sent."""
    text = str(exc).lower()
    if getattr(exc, "status_code", None) not in (400, 422):
        return False
    return (
        "reasoning_effort" in text
        or "unsupported" in text
        or "not supported" in text
        or "unrecognized" in text
        or "unknown" in text
    )


def _chat_completion(client, messages, max_tokens, temperature):
    """One chat completion, dropping reasoning_effort if the model rejects it.

    Building the request separately from `ask_groq` keeps the retry loop above
    readable, and gives unsupported parameters a second chance inside the same
    attempt instead of burning a whole retry on a 400 that never succeeds.
    """
    kwargs = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if GROQ_MODEL in GROQ_REASONING_MODELS:
        kwargs["reasoning_effort"] = GROQ_REASONING_EFFORT
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        if "reasoning_effort" in kwargs and _is_unsupported_param_error(exc):
            kwargs.pop("reasoning_effort")
            return client.chat.completions.create(**kwargs)
        raise


def _is_worth_retrying(exc):
    """Whether another attempt could plausibly succeed.

    A rejected key or a missing model will fail identically every time, so
    retrying only delays the error the user needs to see. Rate limits and
    empty replies are worth another go, and so is a network failure.
    """
    text = str(exc).lower()
    permanent_markers = (
        "invalid api key",
        "incorrect api key",
        "invalid_request_error",
        "permission denied",
        "unauthorized",
        "authentication",
        "model_deprecated",
        "no such model",
        "does not exist",
    )
    if any(marker in text for marker in permanent_markers):
        return False
    # A bad key surfaces from the SDK as a 401 whose body mentions the key
    # being wrong; treat any 401/403 as permanent too.
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return False
    return True


def ask_groq(messages, max_tokens=600, temperature=0.4, attempts=3):
    """Call Groq and return reply text, retrying if the model returns nothing.

    gpt-oss is a reasoning model and can occasionally spend the whole token
    budget on hidden reasoning, answering with an empty string and
    finish_reason="length". That used to reach the user as a blank (then
    silently dropped) message. Retrying with a larger budget turns those
    failures into a normal answer, and the final fallback guarantees the
    caller always gets displayable text.
    """
    last_error = None
    if not GROQ_API_KEY:
        # Guarded rather than left to the SDK: Groq(api_key=None) raises, and
        # that message ("No api_key") does not say where the key should go.
        st.session_state.ai_last_error = describe_groq_key_missing()
        st.session_state.ai_last_stage = "no_api_key"
        return EMPTY_REPLY_MESSAGE

    for attempt in range(attempts):
        st.session_state.ai_last_stage = f"calling_api(attempt {attempt + 1} of {attempts})"
        try:
            client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT_SECONDS)
            response = _chat_completion(
                client,
                messages,
                max_tokens * (attempt + 1),
                temperature,
            )
            reply = (response.choices[0].message.content or "").strip()
            st.session_state.ai_last_reply_len = len(reply)
            st.session_state.ai_last_stage = f"got_reply({len(reply)} chars)"
            if reply:
                # Cleared on success so a later screen does not show a stale
                # error from an earlier question.
                st.session_state.ai_last_error = ""
                return reply
            last_error = RuntimeError("model returned an empty reply")
        except Exception as exc:  # network hiccup, rate limit, bad key, ...
            last_error = exc
            if not _is_worth_retrying(exc):
                # Repeating the same call cannot fix a rejected key, and a
                # short pause between the other retries stops a rate limit from
                # being hammered three times in a row.
                break
        if attempt < attempts - 1:
            time.sleep(1.0 + attempt)
    # The chat ends in st.rerun(), which discards anything written straight to
    # the page. Stashing the reason in session_state is what makes a failure
    # visible on the next run instead of silently becoming a blank bubble.
    st.session_state.ai_last_error = describe_groq_error(last_error)
    st.session_state.ai_last_stage = f"all_attempts_failed({last_error})"
    return EMPTY_REPLY_MESSAGE


def describe_groq_error(exc):
    """Turn a Groq failure into something the user can act on.

    The raw SDK message ("Error code: 401 - {'error': ...}") is the one piece
    of information that distinguishes a rejected key from a rate limit or a
    network problem, so it is kept and explained rather than replaced with a
    generic apology.
    """
    if exc is None:
        return "no reply was returned"
    raw = str(exc).strip()
    lowered = raw.lower()
    if "api key" in lowered or getattr(exc, "status_code", None) in (401, 403):
        return (
            f"Groq rejected the API key {mask_groq_key(GROQ_API_KEY)} ({raw}). "
            f"It came from {GROQ_KEY_SOURCE or 'an unknown place'}, so it is "
            f"invalid, revoked, or has no access to {GROQ_MODEL}. Create a new "
            "key at console.groq.com/keys and update the secret, then restart "
            "the app."
        )
    if "rate limit" in lowered or getattr(exc, "status_code", None) == 429:
        return (
            f"Groq rate limit reached ({raw}). Wait a moment and try again."
        )
    if "model" in lowered and ("not found" in lowered or "deprecat" in lowered):
        return (
            f"Groq does not recognise the model {GROQ_MODEL} ({raw}). Pick a "
            "model from the Groq model list and set GROQ_MODEL."
        )
    return raw or exc.__class__.__name__


def mask_groq_key(key):
    """Show enough of a key to recognise which one is in use, and no more.

    The full value must never reach the page, a log line or a screenshot, so
    only the first and last few characters survive.
    """
    if not key:
        return "(no key)"
    text = str(key)
    if len(text) <= 12:
        return "***"
    return f"{text[:7]}...{text[-4:]}"


def describe_groq_key_missing():
    """Explain exactly where the key belongs for the app that is running.

    This is the single most important message in the whole feature. A local
    run and a Streamlit Community Cloud run take their secrets from two
    completely different places, and editing only one of them is precisely how
    "it works on localhost but the deployed assistant never answers" happens:
    `.streamlit/` is gitignored, so a local secrets.toml is never deployed and
    the hosted app ends up with no key at all.

    Streamlit exposes no reliable "am I running on Community Cloud" flag, and a
    wrong guess here would send the user to the wrong place, so both routes are
    always listed rather than guessing one.
    """
    return (
        "GROQ_API_KEY was not found by the running app, so the assistant has "
        "no model to answer with. Where the key goes depends on how this app "
        "is being served:\n\n"
        "- Deployed on Streamlit Community Cloud (mediscan-ai-cdu.streamlit.app): "
        "open share.streamlit.io -> your app -> Settings -> Secrets, add the "
        'line GROQ_API_KEY = "gsk_...", press Save, then press Rerun. Editing '
        ".streamlit/secrets.toml on your own machine has no effect here, "
        "because that folder is gitignored and never deployed.\n"
        "- Running locally (localhost:8501): add GROQ_API_KEY = \"gsk_...\" to "
        ".streamlit/secrets.toml, or set it as an environment variable, then "
        "restart the app.\n\n"
        f"Checked so far: {GROQ_KEY_PROBLEM or 'no key in the environment or in secrets.toml'}."
    )


def groq_status():
    """Check the configured key and model against the live Groq API.

    Returns (ok, message). Without this, "no key", "revoked key", "retired
    model" and "Groq is unreachable" all look identical from outside the app:
    the assistant simply never answers. One authenticated GET /models is
    cheap enough to run on the assistant screen and separates all four, so the
    deployed app can tell the user what is actually wrong instead of leaving
    them to guess.
    """
    if not GROQ_LIB_AVAILABLE:
        return False, (
            "The `groq` package is not installed, so the assistant cannot "
            "call the API at all. Run `pip install -r requirements.txt`."
        )
    if not GROQ_API_KEY:
        return False, describe_groq_key_missing()

    masked = mask_groq_key(GROQ_API_KEY)
    try:
        client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT_SECONDS)
        model_ids = [getattr(m, "id", "") for m in client.models.list().data]
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        raw = str(exc).strip()
        if status in (401, 403) or "api key" in raw.lower():
            return False, (
                f"Groq rejected the key {masked} ({raw}). It came from "
                f"{GROQ_KEY_SOURCE or 'an unknown place'}. It is invalid, "
                "revoked, or was never given access. Make a new one at "
                "console.groq.com/keys, update the secret, and restart."
            )
        return False, (
            f"Could not reach Groq to verify the key {masked} ({raw}). This "
            "is a network problem rather than a key problem — the assistant "
            "will keep retrying when a question is asked."
        )

    if GROQ_MODEL not in model_ids:
        return False, (
            f"The key {masked} is accepted by Groq, but this account cannot "
            f"use the configured model {GROQ_MODEL!r}. Models available to "
            f"this key: {', '.join(sorted(i for i in model_ids if i))}. Set "
            "GROQ_MODEL in app.py to one of those and redeploy."
        )
    return True, (
        f"Groq connection OK — key {masked} accepted, model {GROQ_MODEL} "
        f"available ({len(model_ids)} models on this key)."
    )

# ============================================================
# AI Assistant — reply languages
# ------------------------------------------------------------
# The assistant can answer in English, Hindi, or Telugu.  This is a
# switch-case style lookup: pick the language the user chose, and build the
# system prompt from the shared safety rules plus that language's
# instruction.  The safety rules stay in English because the model follows
# them more reliably, while the *output* language is what the user reads.
#
# Brand names (Dolo 650, Paracetamol) are deliberately left in Latin script:
# translating a medicine name makes it unsearchable and can point the user at
# a different product.
# ============================================================

ASSISTANT_SAFETY_RULES = (
    "You are a friendly, cautious health-information assistant inside "
    "MediScan AI, an educational prototype. Answer only simple, general "
    "questions about medicines, symptoms, or health habits — for example, "
    "whether a type of medicine is usually taken before or after food, or "
    "general information about a symptom. "
    "Never give specific dosage amounts, never diagnose a condition, and "
    "always remind the user this is general information, not medical "
    "advice — they should consult a doctor or pharmacist for anything "
    "specific to their own health, and seek emergency care immediately "
    "for anything serious or urgent. Keep answers short and clear."
)

ASSISTANT_LANGUAGES = {
    "English": {
        "code": "en",
        "native": "English",
        "instruction": (
            "Write your entire reply in simple, natural English."
        ),
    },
    "हिन्दी": {
        "code": "hi",
        "native": "हिन्दी",
        "instruction": (
            "अपना पूरा जवाब हिन्दी में लिखें — आसान और स्पष्ट शब्दों में। "
            "दवा के ब्रांड नाम और जैव-रासायनिक नाम (जैसे Dolo 650, "
            "Paracetamol, Pantoprazole) अंग्रेज़ी/Latin अक्षरों में ही "
            "रखें, और दवा का नाम बदलकर न लिखें। अगर उपयोगकर्ता ने अंग्रेज़ी "
            "में सवाल पूछा है, तो भी जवाब हिन्दी में ही दें।"
        ),
    },
    "తెలుగు": {
        "code": "te",
        "native": "తెలుగు",
        "instruction": (
            "మీ సమ్పూర్ణ సమాధానాన్ని తెలుగులో రాయండి — సరళమైన, స్పష్టమైన "
            "పదాలతో. మందుల బ్రాండ్ పేర్లు మరియు రసాయనిక పేర్లు (Dolo 650, "
            "Paracetamol, Pantoprazole వంటివి) ఇంగ్లీష్ Latin అక్షరాల్లోనే "
            "ఉంచండి, పేరు మార్చవద్దు. వినియోగదారు ఇంగ్లీష్‌లో అడిగినా సమాధానం "
            "తెలుగులోనే ఇవ్వండి."
        ),
    },
}

# The sidebar language and the reply language spell the same languages
# differently, so the assistant can follow the app language on first use.
ASSISTANT_LANGUAGE_ALIASES = {
    "English": "English",
    "Hindi": "हिन्दी",
    "Telugu": "తెలుగు",
}

# Everything the assistant screen shows, per reply language.
ASSISTANT_UI = {
    "English": {
        "reply_language": "Reply language",
        "title": "AI Health Assistant",
        "subtitle": "Ask general health questions and get clear, cautious information.",
        "disclaimer": (
            "General health information only — not a diagnosis or a substitute "
            "for professional medical advice."
        ),
        "welcome": "Hello, I'm your MediScan AI assistant.",
        "welcome_body": (
            "Ask me about medicines, symptoms, health habits, "
            "or general healthcare information."
        ),
        "chips": (
            "Medicine information",
            "Symptom information",
            "Healthy habits",
        ),
        "ready": "Ready to help",
        "chat_placeholder": "Type your health question here...",
        "clear_chat": "Clear chat",
        "explain_simple": "Explain simply",
        "summarize": "Summarize",
        "regenerate": "Regenerate",
        "translate": "Translate last answer",
        "voice_input": "Voice input",
        "record": "Record",
        "thinking": "MediScan AI is thinking...",
        "you": "You",
        "assistant": "MediScan AI",
        "no_key": (
            "No Groq API key found. Set `GROQ_API_KEY` in your environment "
            "or `.streamlit/secrets.toml`."
        ),
        "no_lib": "The `groq` package isn't installed. Run `pip install groq`.",
        "answer_language_note": "Answers are in",
        "prompts": {
            "explain_simple": "Explain your last answer in very simple words.",
            "summarize": "Summarize your last answer in 3 short bullet points.",
            "regenerate": "Please regenerate your previous answer with clearer wording.",
        },
        "suggestions": [
            "Is Dolo 650 taken before or after food?",
            "What is a common symptom of acidity?",
            "How do I stay hydrated in summer?",
        ],
    },
    "हिन्दी": {
        "reply_language": "जवाब की भाषा",
        "title": "एआई स्वास्थ्य सहायक",
        "subtitle": "सामान्य स्वास्थ्य सवाल पूछें और स्पष्ट जानकारी पाएं।",
        "disclaimer": (
            "यह केवल सामान्य स्वास्थ्य जानकारी है — निदान नहीं, और न ही "
            "डॉक्टर की सलाह का विकल्प।"
        ),
        "welcome": "नमस्ते, मैं आपका MediScan AI सहायक हूं।",
        "welcome_body": (
            "मुझसे दवाओं, लक्षणों, स्वास्थ्य आदतों या सामान्य स्वास्थ्य "
            "जानकारी के बारे में पूछ सकते हैं।"
        ),
        "chips": (
            "दवा की जानकारी",
            "लक्षणों की जानकारी",
            "स्वस्थ आदतें",
        ),
        "ready": "मदद के लिए तैयार",
        "chat_placeholder": "अपना स्वास्थ्य सवाल यहां लिखें...",
        "clear_chat": "चैट साफ़ करें",
        "explain_simple": "आसान भाषा में समझाएं",
        "summarize": "सारांश बताएं",
        "regenerate": "फिर से लिखें",
        "translate": "पिछले जवाब का अनुवाद करें",
        "voice_input": "वॉइस इनपुट",
        "record": "रिकॉर्ड करें",
        "thinking": "MediScan AI सोच रहा है...",
        "you": "आप",
        "assistant": "MediScan AI",
        "no_key": (
            "Groq API कुंजी नहीं मिली। अपने एनवायरनमेंट या "
            "`.streamlit/secrets.toml` में `GROQ_API_KEY` सेट करें।"
        ),
        "no_lib": "`groq` पैकेज इंस्टॉल नहीं है। `pip install groq` चलाएं।",
        "answer_language_note": "जवाब की भाषा:",
        "prompts": {
            "explain_simple": "अपने पिछले जवाब को बहुत आसान शब्दों में समझाएं।",
            "summarize": "अपने पिछले जवाब को 3 छोटी बिंदुओं में सारांश बताएं।",
            "regenerate": "कृपया अपने पिछले जवाब को और स्पष्ट शब्दों में फिर से लिखें।",
        },
        "suggestions": [
            "Dolo 650 दवा खाने से पहले लें या बाद में?",
            "अम्लता के आम लक्षण क्या हैं?",
            "गर्मी में शरीर में पानी कैसे बनाए रखें?",
        ],
    },
    "తెలుగు": {
        "reply_language": "సమాధాన భాష",
        "title": "AI ఆరోగ్య సహాయకుడు",
        "subtitle": "సాధారణ ఆరోగ్య ప్రశ్నలు అడిగి స్పష్టమైన సమాచారం పొందండి.",
        "disclaimer": (
            "ఇది సాధారణ ఆరోగ్య సమాచారం మాత్రమే — నిర్ధారణ కాదు, వైద్య "
            "సలహాకు ప్రత్యామాయ కాదు."
        ),
        "welcome": "నమస్కారం, నేను మీ MediScan AI సహాయకుడను.",
        "welcome_body": (
            "మందులు, లక్షణాలు, ఆరోగ్య అలవాట్లు లేదా సాధారణ వైద్య సమాచారం "
            "గురించి నన్ను అడగవచ్చు."
        ),
        "chips": (
            "మందుల సమాచారం",
            "లక్షణాల సమాచారం",
            "ఆరోగ్య అలవాట్లు",
        ),
        "ready": "సహాయం చేయడానికి సిద్ధం",
        "chat_placeholder": "మీ ఆరోగ్య ప్రశ్నను ఇక్కడ టైప్ చేయండి...",
        "clear_chat": "చాట్ తొలగించు",
        "explain_simple": "సరళంగా వివరించు",
        "summarize": "సారాంశం చెప్పు",
        "regenerate": "మళ్ళీ రాయి",
        "translate": "మునుపటి సమాధానాన్ని అనువదించు",
        "voice_input": "వాయిస్ ఇన్‌పుట్",
        "record": "రికార్డ్ చేయి",
        "thinking": "MediScan AI ఆలోచిస్తోంది...",
        "you": "మీరు",
        "assistant": "MediScan AI",
        "no_key": (
            "Groq API కీ కనబడలేదు. మీ ఎన్‌విరాన్‌మెంట్ లేదా "
            "`.streamlit/secrets.toml` లో `GROQ_API_KEY` సెట్ చేయండి."
        ),
        "no_lib": "`groq` ప্যাকేజీ ఇంస్టాల్ కాలేదు. `pip install groq` నడపండి.",
        "answer_language_note": "సమాధాన భాష:",
        "prompts": {
            "explain_simple": "మీ మునుపటి సమాధానాన్ని చాలా సరళమైన పదాల్లో వివరించండి.",
            "summarize": "మీ మునుపటి సమాధానాన్ని 3 చిన్న అంశాలుగా సారాంశం చెప్పండి.",
            "regenerate": "మీ మునుపటి సమాధానాన్ని స్పష్టమైన పదాలతో మళ్ళీ రాయండి.",
        },
        "suggestions": [
            "Dolo 650 మందిని భోజనం ముందు తీసుకోవాలా లేదా తర్వాత?",
            "అమ్లత గాంట్ల ఉండే సాధారణ లక్షణాలు ఏమిటి?",
            "వేసummerలో శరీరంలో నీరు ఎలా ఉంచుకోవాలి?"
            .replace("వేసummerలో", "వేస్తిలో"),
        ],
    },
}


def assistant_language_name(app_language: str) -> str:
    """Map a sidebar language onto an assistant reply language."""
    return ASSISTANT_LANGUAGE_ALIASES.get(app_language, "English")


def assistant_ui(language_name: str) -> dict:
    return ASSISTANT_UI.get(language_name, ASSISTANT_UI["English"])


def assistant_code(language_name: str) -> str:
    return ASSISTANT_LANGUAGES.get(language_name, ASSISTANT_LANGUAGES["English"])["code"]


def build_assistant_prompt(language_name: str) -> str:
    """Build the system prompt for one reply language (the switch case)."""
    config = ASSISTANT_LANGUAGES.get(language_name) or ASSISTANT_LANGUAGES["English"]
    return (
        ASSISTANT_SAFETY_RULES
        + " "
        + config["instruction"]
        + " Always write the closing reminder about seeing a doctor in that "
        "same language too, and keep the whole reply under 150 words."
    )


def build_translation_prompt(text: str, language_name: str) -> str:
    """Prompt used by the 'translate last answer' control."""
    config = ASSISTANT_LANGUAGES.get(language_name) or ASSISTANT_LANGUAGES["English"]
    return (
        "Translate the following health-information answer into "
        f"{config['native']} ({config['code']}). Keep every medicine brand or "
        "chemical name in Latin script exactly as written, keep the meaning and "
        "the medical disclaimer unchanged, and output only the translation.\n\n"
        f"{text}"
    )


# Medicine lookups are grounded on openFDA/RxNorm/Wikipedia data, and the model
# is only asked to explain that data (or, for names no database knows, to say
# what the name usually refers to).  It still must not invent a dosage.
def build_medicine_prompt(name: str, facts: dict, language_name: str) -> str:
    """Prompt for the plain-language summary shown under a medicine result."""
    config = ASSISTANT_LANGUAGES.get(language_name) or ASSISTANT_LANGUAGES["English"]
    known = facts.get("matched")
    lines = [
        f"Medicine name searched: {name}",
        "",
        "Reference data gathered from public drug databases:",
    ]
    if known:
        for key, label in (
            ("matched_name", "Closest database match"),
            ("generic_name", "Generic name"),
            ("substance", "Active substance"),
            ("drug_class", "Drug class"),
            ("form", "Form"),
            ("purpose", "Purpose"),
            ("uses", "What it is used for"),
            ("side_effects", "Common side effects"),
            ("warnings", "Warnings"),
        ):
            value = facts.get(key)
            if value:
                lines.append(f"- {label}: {value}")
    else:
        lines.append(
            "- No public drug database recognised this name. It may be an "
            "Indian brand name, a local formulation, or a misspelling."
        )
    lines += [
        "",
        "Write a short plain-language summary for a patient (5 bullet points, "
        "under 120 words) that:",
        "1. says what the medicine is generally used for,",
        "2. says whether it is usually taken before or after food, if that is "
        "generally known, and never gives a dose, tablet count, or strength,",
        "3. mentions the two most common side effects or precautions, if known,",
        "4. is honest if you are not sure what this name refers to, and",
        f"5. is written entirely in {config['native']} ({config['code']}), "
        "keeping the medicine name in Latin script.",
        "If you are not sure this medicine exists, say so plainly instead of "
        "guessing. Do not add a diagnosis or treatment plan.",
    ]
    return "\n".join(lines)


ASSISTANT_SYSTEM_PROMPT = build_assistant_prompt("English")


# ============================================================
# Auth session state
# ============================================================

if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_name" not in st.session_state:
    st.session_state.user_name = None
if "username" not in st.session_state:
    st.session_state.username = None
if "emergency_contact" not in st.session_state:
    st.session_state.emergency_contact = {"name": "", "phone": "", "relation": ""}


# ============================================================
# Refresh-proof sign-in
# ------------------------------------------------------------
# Reloading the page used to hand Streamlit a brand-new session, wiping
# st.session_state and throwing the visitor back to the login screen on the
# Home page. The signed-in visitor is now remembered by a token in a
# long-lived cookie: its SHA-256 digest is matched against the `sessions`
# table and the account is rehydrated here, before the login gate below runs.
# Their documents, chat, reminders and saved medicines are already reloaded
# from SQLite further down (load_local_user_data), so restoring the identity
# and the current page is enough to bring the whole app back.
# ============================================================

PERSIST_COOKIE_NAME = "mediscan_session"
PERSIST_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
PERSIST_TOKEN_TTL = 60 * 60 * 24 * 30       # 30 days

# Only pages the app actually renders are restored, so a stale or edited
# value can never leave the visitor staring at a blank screen.
PERSISTED_FEATURES = frozenset({
    "Home", "Search", "Triage", "Medicine Scanner", "AI Assistant",
    "Hospital Finder", "Reminders", "History", "Documents", "Profile",
    "Notifications", "Privacy & Security", "Accessibility",
    "Loading Spinners",
})

# Display preferences that live in session_state and would otherwise reset.
PERSISTED_UI_FLAGS = ("accessibility_large_text", "accessibility_high_contrast")


def _persist_token_hash(token):
    """Hash a browser token the same way the reset tokens are hashed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_persist_cookie():
    """Read the token the browser sent with this page load, if any.

    st.context.cookies only reflects what the browser sent when the page
    loaded, so this returns nothing until the next reload — that is expected.
    """
    try:
        return st.context.cookies.get(PERSIST_COOKIE_NAME)
    except Exception:
        # Older Streamlit builds have no st.context.cookies; the app then
        # simply keeps its previous reload behaviour instead of failing.
        return None


def _write_persist_cookie(js):
    """Run a cookie write inside a same-origin, zero-height component frame.

    Streamlit has no server-side cookie API, and the cookie has to be written
    by the browser, so this uses a sandboxed-but-same-origin iframe that shares
    the app's origin. The frame is collapsed to nothing so it is invisible.
    """
    components.html(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;height:0;overflow:hidden;"
        "background:transparent}</style></head>"
        f"<body><script>{js}</script></body></html>",
        height=0,
    )


def _set_persist_cookie(token):
    _write_persist_cookie(
        "document.cookie = %s;"
        % json.dumps(
            f"{PERSIST_COOKIE_NAME}={token};"
            f"path=/;max-age={PERSIST_COOKIE_MAX_AGE};SameSite=Lax"
        )
    )


def _clear_persist_cookie():
    _write_persist_cookie(
        "document.cookie = %s;"
        % json.dumps(f"{PERSIST_COOKIE_NAME}=;path=/;max-age=0;SameSite=Lax")
    )


def _current_ui_state():
    """The slice of interface state that should survive a page reload."""
    state = {"active_feature": st.session_state.get("active_feature", "Home")}
    for flag in PERSISTED_UI_FLAGS:
        state[flag] = bool(st.session_state.get(flag, False))
    return state


def issue_persist_token(user_id):
    """Store a fresh token digest for a signed-in visitor and return the token.

    Only the digest is written, so a copy of the database cannot be used to
    impersonate anyone. Returns None (after warning) if the write fails, which
    leaves the visitor signed in for this page load only.
    """
    token = secrets.token_urlsafe(32)
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO sessions (user_id, token_hash, ui_state, expires_at, last_active)
               VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                user_id,
                _persist_token_hash(token),
                json.dumps(_current_ui_state()),
                int(time.time()) + PERSIST_TOKEN_TTL,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        st.warning(f"Could not start a refresh-proof session: {exc}")
        return None
    st.session_state.persist_token = token
    st.session_state.persist_cookie_written = False
    return token


def save_persist_ui_state():
    """Record which page the visitor is on, so a reload returns them to it."""
    token = st.session_state.get("persist_token")
    if not token or not st.session_state.get("user_id"):
        return
    try:
        conn = get_connection()
        conn.execute(
            """UPDATE sessions SET ui_state = ?, last_active = CURRENT_TIMESTAMP
               WHERE token_hash = ?""",
            (json.dumps(_current_ui_state()), _persist_token_hash(token)),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Remembering the page is never worth interrupting the app for.
        pass


def drop_persist_token():
    """Forget this browser's token on sign-out and on account deletion."""
    token = st.session_state.pop("persist_token", None)
    st.session_state.pop("persist_cookie_written", None)
    # Both callers st.rerun() straight afterwards, which can discard the
    # component carrying the cookie delete, so ask the next run to repeat it.
    st.session_state.persist_cookie_clear_pending = True
    _clear_persist_cookie()
    if not token:
        return
    try:
        conn = get_connection()
        conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (_persist_token_hash(token),),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def restore_persist_session():
    """Sign the visitor back in from the cookie their last page load left.

    Runs once per Streamlit session, before the login gate. The browser only
    resends cookies when it makes a new request, so signing out leaves a stale
    cookie in st.context for the rest of this session: `persist_restored` stays
    True and this function does not run again until the next reload, by which
    time the token has been deleted and the cookie cleared.
    """
    if st.session_state.get("persist_restored") or st.session_state.get("user_id"):
        st.session_state.persist_restored = True
        return
    st.session_state.persist_restored = True

    token = _read_persist_cookie()
    if not token:
        return

    try:
        conn = get_connection()
        row = conn.execute(
            """SELECT s.user_id, s.ui_state, u.name, u.username
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (_persist_token_hash(token), int(time.time())),
        ).fetchone()
        conn.close()
    except Exception:
        return

    # No row means the token is unknown or expired, or the account is gone.
    if not row:
        return

    st.session_state.persist_token = token
    st.session_state.user_id = row["user_id"]
    st.session_state.user_name = row["name"]
    st.session_state.username = row["username"]

    try:
        ui_state = json.loads(row["ui_state"] or "{}")
    except (TypeError, ValueError):
        ui_state = {}

    if ui_state.get("active_feature") in PERSISTED_FEATURES:
        st.session_state.active_feature = ui_state["active_feature"]
    for flag in PERSISTED_UI_FLAGS:
        if flag in ui_state:
            st.session_state[flag] = bool(ui_state[flag])
    # emergency_contact is intentionally left alone: load_local_user_data()
    # reads the same emergency_contacts row further down.


restore_persist_session()

# Cookie writes are (re-)issued here rather than where they are requested,
# because both sign-in and sign-out end in st.rerun(), which can discard the
# component that carries the write. This run therefore replaces the write that
# was cut short, and on later loads it slides the expiry forward.
if st.session_state.pop("persist_cookie_clear_pending", False):
    _clear_persist_cookie()
elif st.session_state.get("persist_token") and not st.session_state.get("persist_cookie_written"):
    st.session_state.persist_cookie_written = True
    _set_persist_cookie(st.session_state.persist_token)


# Widget state must be cleared before Streamlit recreates login/account widgets
# on the run after logout.  Keep this list explicit:
# clearing arbitrary session keys can remove Streamlit's internal bookkeeping.
VISITOR_WIDGET_KEYS = (
    "login_identifier",
    "login_password",
    "forgot_email",
    "reset_code_input",
    "reset_new_password",
    "reset_confirm_password",
    "reg_username",
    "reg_name",
    "reg_email",
    "reg_password",
    "new_username",
    "new_email",
    "current_password",
    "new_password",
    "confirm_password",
    "global_search_input",
    "ai_voice_input",
    "ai_chat_input",
    "assistant_language_switch",
    "medicine_web_language",
    "medical_document_uploader",
    "medicine_photo_uploader",
    "medicine_camera",
    "medicine_manual_query",
    "medicine_web_toggle",
    "triage_symptoms",
    "triage_age",
    "triage_duration",
    "triage_severity",
    "hospital_city",
    "hospital_region",
    "hospital_district",
    "procedure_search",
    "selected_procedure",
    "reminder_name",
    "reminder_form",
    "reminder_food",
    "reminder_time",
    "reminder_notes",
    "emergency_contact_name",
    "emergency_contact_phone",
    "emergency_contact_relation",
    "language_select",
    "privacy_delete_confirm",
    "accessibility_large_text",
    "accessibility_high_contrast",
)


def clear_visitor_widget_state():
    """Remove values entered by the previous visitor before widgets rerun."""
    for key in VISITOR_WIDGET_KEYS:
        st.session_state.pop(key, None)


if st.session_state.pop("clear_visitor_widgets_on_next_run", False):
    clear_visitor_widget_state()


def clear_local_session_state():
    """Clear credentials and every cached record belonging to one visitor."""
    st.session_state.user_id = None
    st.session_state.user_name = None
    st.session_state.username = None
    st.session_state.clear_visitor_widgets_on_next_run = True
    st.session_state.auth_page = "login"
    for key in ("reset_code", "reset_email", "reset_user_id", "reset_verified"):
        st.session_state.pop(key, None)
    st.session_state.emergency_contact = {
        "name": "",
        "phone": "",
        "relation": "",
    }
    st.session_state.documents = []
    st.session_state.chat_messages = []
    st.session_state.reminders = []
    st.session_state.activity_log = []
    st.session_state.saved_medicines = []
    st.session_state.notifications = []
    st.session_state.triage_result = None
    st.session_state.assistant_language = "English"
    st.session_state.medicine_lookup = None
    st.session_state.medicine_ai_summary = None
    st.session_state.prefill_reminder = None
    st.session_state.ai_prefill = None
    st.session_state.ai_regenerate = False
    st.session_state.reminder_alerts_fired = set()
    st.session_state.active_feature = "Home"

# ============================================================
# Login / Register gate
# ------------------------------------------------------------
# Everything below this block only runs once user_id is set.
# There is exactly ONE login flow and ONE forgot-password flow —
# do not duplicate this block below st.stop().
# ============================================================

if st.session_state.user_id is None:

    # -------------------- PROFESSIONAL AUTH --------------------
    if "auth_page" not in st.session_state:
        st.session_state.auth_page = "login"

    # Small centered logo above the authentication card.
    _logo_url = logo_data_url()
    st.markdown(
        f"""
        <div class="auth-logo-center">
            <img src="{_logo_url}" alt="MediScan AI" />
        </div>
        """,
        unsafe_allow_html=True
    )

    # -------------------- REGISTER --------------------
    if st.session_state.auth_page == "register":

        st.markdown(
            """
            <div class="auth-card-top">
                <div class="auth-card-kicker">GET STARTED</div>
                <h1>Create Your Account</h1>
                <p>Join MediScan AI for better, simpler healthcare.</p>
            </div>
            """,
            unsafe_allow_html=True
        )

        reg_username = st.text_input(
            "Username",
            key="reg_username",
            placeholder="Choose a unique username"
        )
        reg_name = st.text_input(
            "Full Name",
            key="reg_name",
            placeholder="Enter your full name"
        )
        reg_email = st.text_input(
            "Email",
            key="reg_email",
            placeholder="Enter your email"
        )
        reg_password = st.text_input(
            "Password",
            type="password",
            key="reg_password",
            placeholder="Create a strong password"
        )

        if st.button(
            "Create Account",
            key="create_account_btn",
            use_container_width=True
        ):
            if reg_username and reg_name and reg_email and reg_password:
                if len(reg_password) < 8:
                    st.warning("Use a password with at least 8 characters.")
                elif not re.fullmatch(r"[a-z0-9_]{3,30}", reg_username.strip().lower()):
                    st.warning(
                        "Username must be 3–30 characters using letters, numbers, or underscores."
                    )
                else:
                    username = reg_username.strip().lower()
                    email = reg_email.strip().lower()
                    conn = get_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute(
                            """
                            INSERT INTO users (username, name, email, password_hash)
                            VALUES (?, ?, ?, ?)
                            """,
                            (username, reg_name.strip(), email, hash_password(reg_password)),
                        )
                        conn.commit()
                        st.success("Account created successfully. You can now login.")
                        st.session_state.auth_page = "login"
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Username or email is already registered.")
                    finally:
                        conn.close()
            else:
                st.warning("Please fill all fields.")

        st.markdown(
            "<div class='auth-switch-label'>Already have an account?</div>",
            unsafe_allow_html=True
        )

        if st.button(
            "Login",
            key="auth_switch_login",
            use_container_width=True
        ):
            st.session_state.auth_page = "login"
            st.rerun()

    # -------------------- LOGIN --------------------
    else:

        st.markdown(
            """
            <div class="auth-card-top">
                <div class="auth-card-kicker">WELCOME BACK</div>
                <h1>Welcome Back</h1>
                <p>Login to continue to your MediScan AI account.</p>
            </div>
            """,
            unsafe_allow_html=True
        )

        login_identifier = st.text_input(
            "Username or Email",
            key="login_identifier",
            placeholder="Enter your username or email"
        )

        login_password = st.text_input(
            "Password",
            type="password",
            key="login_password",
            placeholder="Enter your password"
        )

        st.markdown(
            "<div class='auth-forgot-label'>Forgot your password?</div>",
            unsafe_allow_html=True
        )

        if st.button(
            "Login",
            key="login_btn",
            use_container_width=True
        ):
            identifier = login_identifier.strip().lower()
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, username, name, password_hash
                FROM users
                WHERE lower(username) = ? OR lower(email) = ?
                """,
                (identifier, identifier),
            )
            user = cursor.fetchone()
            conn.close()

            if user and verify_password(login_password, user["password_hash"]):
                # Upgrade legacy local SHA-256 hashes after a successful
                # login; do not retain the old fast hash.
                if not str(user["password_hash"]).startswith("pbkdf2_sha256$"):
                    conn = get_connection()
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(login_password), user["id"]),
                    )
                    conn.commit()
                    conn.close()
                st.session_state.user_id = user["id"]
                st.session_state.user_name = user["name"]
                st.session_state.username = user["username"]
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, phone, relation FROM emergency_contacts WHERE user_id = ?",
                    (user["id"],),
                )
                contact = cursor.fetchone()
                conn.close()
                st.session_state.emergency_contact = (
                    {
                        "name": contact["name"],
                        "phone": contact["phone"],
                        "relation": contact["relation"],
                    }
                    if contact
                    else {"name": "", "phone": "", "relation": ""}
                )
                st.session_state.active_feature = "Home"
                # Remember this browser so a page reload signs them back in.
                _new_token = issue_persist_token(user["id"])
                if _new_token:
                    _set_persist_cookie(_new_token)
                st.rerun()
            else:
                st.error("Invalid username/email or password.")

        with st.expander("Reset Password"):

            forgot_email = st.text_input(
                "Registered Email",
                key="forgot_email"
            )

            if st.button(
                "Send Reset Code",
                key="send_reset_code"
            ):
                if not forgot_email.strip():
                    st.warning("Please enter your registered email.")
                else:
                    reset_email = forgot_email.strip().lower()
                    reset_token = issue_local_reset_token(reset_email)
                    # Always show the same flow so the reset step does not
                    # disclose whether an email is registered. In a hosted
                    # deployment this token would be delivered by email/SMS.
                    st.session_state.reset_email = reset_email
                    st.session_state.reset_code = reset_token
                    st.session_state.reset_verified = False
                    st.info(
                        "Development reset token (valid for 15 minutes): "
                        f"{reset_token}"
                    )

            if st.session_state.get("reset_code"):
                entered_code = st.text_input(
                    "Enter Reset Code",
                    key="reset_code_input"
                )

                if st.button(
                    "Verify Code",
                    key="verify_reset_code"
                ):
                    user_id = consume_local_reset_token(
                        st.session_state.reset_email,
                        entered_code,
                    )
                    if user_id is not None:
                        st.session_state.reset_user_id = user_id
                        st.session_state.reset_verified = True
                        st.success("Code verified.")
                    else:
                        st.error("Invalid or expired reset code.")

            if st.session_state.get("reset_verified", False):
                new_password = st.text_input(
                    "New Password",
                    type="password",
                    key="reset_new_password"
                )
                confirm_password = st.text_input(
                    "Confirm New Password",
                    type="password",
                    key="reset_confirm_password"
                )

                if st.button(
                    "Reset Password",
                    key="reset_password"
                ):
                    if not new_password or not confirm_password:
                        st.warning("Please fill both password fields.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        conn = get_connection()
                        cursor = conn.cursor()

                        cursor.execute(
                            """
                            UPDATE users
                            SET password_hash = ?
                            WHERE id = ?
                            """,
                            (
                                hash_password(new_password),
                                st.session_state.reset_user_id,
                            )
                        )
                        cursor.execute(
                            """
                            UPDATE password_reset_tokens
                            SET used_at = ?
                            WHERE user_id = ? AND used_at IS NULL
                            """,
                            (int(time.time()), st.session_state.reset_user_id),
                        )

                        conn.commit()
                        conn.close()

                        st.success("Password reset successfully.")

                        st.session_state.pop("reset_code", None)
                        st.session_state.pop("reset_email", None)
                        st.session_state.pop("reset_user_id", None)
                        st.session_state.pop("reset_verified", None)

        st.markdown(
            "<div class='auth-switch-label'>Don't have an account?</div>",
            unsafe_allow_html=True
        )

        if st.button(
            "Create Account",
            key="auth_switch_register",
            use_container_width=True
        ):
            st.session_state.auth_page = "register"
            st.rerun()

    st.markdown(
        """
        <div class="auth-security-note">
            <span class="auth-shield">✓</span>
            <span>Your information stays within your MediScan AI account.</span>
        </div>
        <div class="auth-footer">
            MediScan AI provides general health information and is not a
            substitute for professional medical advice.
        </div>
        """,
        unsafe_allow_html=True
    )

    st.stop()

# ============================================================
# From here on, the user is logged in.
# ============================================================

# Small branded logo in the top-right of the logged-in app.
# It is intentionally part of the normal document flow (not fixed),
# so it scrolls naturally with the page.
_logo_url = logo_data_url()
st.markdown(
    f"""<div class="ms-app-logo-row"><div class="ms-app-logo"><img src="{_logo_url}" alt="MediScan AI" /></div></div>""",
    unsafe_allow_html=True
)

if st.session_state.get("accessibility_large_text", False):
    st.markdown("<style>html, body, .stApp {font-size: 17px !important;} .stMarkdown, .stTextInput, .stSelectbox, .stTextArea {font-size: 1.05rem !important;}</style>", unsafe_allow_html=True)
if st.session_state.get("accessibility_high_contrast", False):
    st.markdown("<style>.stApp {filter: contrast(1.12);} .ms-stat-card,.ms-activity-row,.ms-search-result,.ms-notification-row {border-width:2px !important;}</style>", unsafe_allow_html=True)

# ============================================================
# Multi-language support (simple dictionary based)
# ------------------------------------------------------------
# NOTE: this only translates the app's static labels, not the
# dataset content (procedure names, hospital names, symptoms
# you type in stay in whatever language you type them in).
# Translations below were machine-translated — have a native
# speaker review them before a real submission/demo.
# ============================================================

TRANSLATIONS = {
    "English": {
        "app_title": "MediScan AI",
        "app_subtitle": "Healthcare Price Transparency & Triage",
        "tab_triage": "Symptom Triage",
        "tab_hospitals": "Hospital Finder",
        "tab_scanner": "Medicine Scanner",
        "tab_assistant": "AI Assistant",
        "tab_documents": "My Documents",
        "tab_history": "Past History",
        "describe_symptoms": "Describe your symptoms",
        "age": "Age",
        "duration_q": "How long have you had these symptoms?",
        "severity_q": "How severe are your symptoms?",
        "analyze_btn": "🔍 Analyze Symptoms",
        "select_city": "🏙️ Select City",
        "select_region": "🗺️ Select Revenue District",
        "select_district": "📍 Select District / Mandal",
        "select_procedure": "🧪 Select Medical Test / Procedure",
        "best_hospital": "🏆 Best Hospital For You",
        "in_district": "In Your District",
        "other_district": "Other Mandals in This Revenue District (out of surroundings)",
        "emergency_contact": "Emergency Contact",
        "upload_docs": "Upload your medical documents (prescriptions, past reports)",
        "history_empty": "No past checks yet. Run a symptom check to see it here.",
        "diet_suggestion_title": "🥗 General Diet Suggestion",
        "location_link_text": "📍 View on Map",
        "scan_medicine_btn": "🔍 Identify Medicine",
        "add_reminder_btn": "➕ Add Reminder",
        "ask_assistant_placeholder": "Ask a simple health or medicine question...",
    },
    "Hindi": {
        "app_title": "🩺 मेडिस्कैन एआई",
        "app_subtitle": "स्वास्थ्य मूल्य पारदर्शिता और ट्राइएज",
        "tab_triage": "🩺 लक्षण जांच",
        "tab_hospitals": "🏥 अस्पताल खोजें",
        "tab_scanner": "📷 दवा स्कैनर",
        "tab_assistant": "🤖 एआई सहायक और रिमाइंडर",
        "tab_documents": "📁 मेरे दस्तावेज़",
        "tab_history": "🕑 पिछला इतिहास",
        "describe_symptoms": "अपने लक्षण बताएं",
        "age": "उम्र",
        "duration_q": "आपको ये लक्षण कब से हैं?",
        "severity_q": "आपके लक्षण कितने गंभीर हैं?",
        "analyze_btn": "🔍 लक्षण जांचें",
        "select_city": "🏙️ शहर चुनें",
        "select_region": "🗺️ राजस्व ज़िला चुनें",
        "select_district": "📍 ज़िला / मंडल चुनें",
        "select_procedure": "🧪 जांच / प्रक्रिया चुनें",
        "best_hospital": "🏆 आपके लिए सबसे अच्छा अस्पताल",
        "in_district": "आपके ज़िले में",
        "other_district": "इस राजस्व ज़िले के अन्य मंडल",
        "emergency_contact": "आपातकालीन संपर्क",
        "upload_docs": "अपने मेडिकल दस्तावेज़ अपलोड करें (पर्चे, पुरानी रिपोर्ट)",
        "history_empty": "अभी तक कोई जांच नहीं हुई। लक्षण जांचें ताकि यह यहां दिखे।",
        "diet_suggestion_title": "🥗 सामान्य आहार सुझाव",
        "location_link_text": "📍 मैप पर देखें",
        "scan_medicine_btn": "🔍 दवा पहचानें",
        "add_reminder_btn": "➕ रिमाइंडर जोड़ें",
        "ask_assistant_placeholder": "एक सरल स्वास्थ्य सवाल पूछें...",
    },
    "Telugu": {
        "app_title": "🩺 మెడిస్కాన్ AI",
        "app_subtitle": "ఆరోగ్య ధర పారదర్శకత & ట్రయాజ్",
        "tab_triage": "🩺 లక్షణాల పరిశీలన",
        "tab_hospitals": "🏥 ఆసుపత్రి వెతుకు",
        "tab_scanner": "📷 మెడిసిన్ స్కానర్",
        "tab_assistant": "🤖 AI సహాయకుడు & రిమైండర్",
        "tab_documents": "📁 నా పత్రాలు",
        "tab_history": "🕑 గత చరిత్ర",
        "describe_symptoms": "మీ లక్షణాలు వివరించండి",
        "age": "వయస్సు",
        "duration_q": "ఈ లక్షణాలు ఎంత కాలంగా ఉన్నాయి?",
        "severity_q": "మీ లక్షణాలు ఎంత తీవ్రంగా ఉన్నాయి?",
        "analyze_btn": "🔍 లక్షణాలను విశ్లేషించండి",
        "select_city": "🏙️ నగరం ఎంచుకోండి",
        "select_region": "🗺️ రెవెన్యూ జిల్లా ఎంచుకోండి",
        "select_district": "📍 జిల్లా / మండలం ఎంచుకోండి",
        "select_procedure": "🧪 పరీక్ష / ప్రొసీజర్ ఎంచుకోండి",
        "best_hospital": "🏆 మీకు ఉత్తమ ఆసుపత్రి",
        "in_district": "మీ జిల్లాలో",
        "other_district": "ఈ రెవెన్యూ జిల్లాలోని ఇతర మండలాలు",
        "emergency_contact": "అత్యవసర సంప్రదింపు",
        "upload_docs": "మీ వైద్య పత్రాలు అప్‌లోడ్ చేయండి (ప్రిస్క్రిప్షన్లు, పాత రిపోర్టులు)",
        "history_empty": "ఇంకా పరిశీలనలు లేవు. లక్షణాలు పరిశీలించి ఇక్కడ చూడండి.",
        "diet_suggestion_title": "🥗 సాధారణ ఆహార సూచన",
        "location_link_text": "📍 మ్యాప్‌లో చూడండి",
        "scan_medicine_btn": "🔍 మెడిసిన్ గుర్తించండి",
        "add_reminder_btn": "➕ రిమైండర్ జోడించండి",
        "ask_assistant_placeholder": "సాధారణ ఆరోగ్య ప్రశ్న అడగండి...",
    },
    "Bengali": {
        "app_title": "🩺 মেডিস্ক্যান এআই",
        "app_subtitle": "স্বাস্থ্য মূল্য স্বচ্ছতা ও ট্রায়াজ",
        "tab_triage": "🩺 লক্ষণ পরীক্ষা",
        "tab_hospitals": "🏥 হাসপাতাল খুঁজুন",
        "tab_scanner": "📷 ওষুধ স্ক্যানার",
        "tab_assistant": "🤖 এআই সহায়ক ও রিমাইন্ডার",
        "tab_documents": "📁 আমার ডকুমেন্টস",
        "tab_history": "🕑 পুরনো ইতিহাস",
        "describe_symptoms": "আপনার লক্ষণ বর্ণনা করুন",
        "age": "বয়স",
        "duration_q": "আপনার এই লক্ষণ কতদিন ধরে আছে?",
        "severity_q": "আপনার লক্ষণ কতটা গুরুতর?",
        "analyze_btn": "🔍 লক্ষণ বিশ্লেষণ করুন",
        "select_city": "🏙️ শহর নির্বাচন করুন",
        "select_region": "🗺️ রেভিনিউ জেলা নির্বাচন করুন",
        "select_district": "📍 জেলা / মন্ডল নির্বাচন করুন",
        "select_procedure": "🧪 মেডিকেল টেস্ট নির্বাচন করুন",
        "best_hospital": "🏆 আপনার জন্য সেরা হাসপাতাল",
        "in_district": "আপনার জেলায়",
        "other_district": "এই রেভিনিউ জেলার অন্য মন্ডল",
        "emergency_contact": "জরুরি যোগাযোগ",
        "upload_docs": "আপনার মেডিকেল ডকুমেন্ট আপলোড করুন",
        "history_empty": "এখনো কোনো পরীক্ষা নেই।",
        "diet_suggestion_title": "🥗 সাধারণ খাদ্য পরামর্শ",
        "location_link_text": "📍 মানচিত্রে দেখুন",
        "scan_medicine_btn": "🔍 ওষুধ সনাক্ত করুন",
        "add_reminder_btn": "➕ রিমাইন্ডার যুক্ত করুন",
        "ask_assistant_placeholder": "একটি সহজ স্বাস্থ্য প্রশ্ন করুন...",
    },
    "Marathi": {
        "app_title": "🩺 मेडिस्कॅन एआय",
        "app_subtitle": "आरोग्य किंमत पारदर्शकता आणि ट्रायाज",
        "tab_triage": "🩺 लक्षण तपासणी",
        "tab_hospitals": "🏥 रुग्णालय शोधा",
        "tab_scanner": "📷 औषध स्कॅनर",
        "tab_assistant": "🤖 एआय सहाय्यक आणि आठवण",
        "tab_documents": "📁 माझी कागदपत्रे",
        "tab_history": "🕑 मागील इतिहास",
        "describe_symptoms": "तुमची लक्षणे सांगा",
        "age": "वय",
        "duration_q": "तुम्हाला ही लक्षणे किती दिवस आहेत?",
        "severity_q": "तुमची लक्षणे किती गंभीर आहेत?",
        "analyze_btn": "🔍 लक्षणे तपासा",
        "select_city": "🏙️ शहर निवडा",
        "select_region": "🗺️ महसूल जिल्हा निवडा",
        "select_district": "📍 जिल्हा / मंडळ निवडा",
        "select_procedure": "🧪 वैद्यकीय चाचणी निवडा",
        "best_hospital": "🏆 तुमच्यासाठी सर्वोत्तम रुग्णालय",
        "in_district": "तुमच्या जिल्ह्यात",
        "other_district": "या महसूल जिल्ह्यातील इतर मंडळे",
        "emergency_contact": "आपत्कालीन संपर्क",
        "upload_docs": "तुमची वैद्यकीय कागदपत्रे अपलोड करा",
        "history_empty": "अजून तपासणी नाही.",
        "diet_suggestion_title": "🥗 सामान्य आहार सल्ला",
        "location_link_text": "📍 नकाशावर पहा",
        "scan_medicine_btn": "🔍 औषध ओळखा",
        "add_reminder_btn": "➕ आठवण जोडा",
        "ask_assistant_placeholder": "साधा आरोग्य प्रश्न विचारा...",
    },
    "Tamil": {
        "app_title": "🩺 மெடிஸ்கேன் AI",
        "app_subtitle": "சுகாதார விலை வெளிப்படைத்தன்மை & ட்ரையேஜ்",
        "tab_triage": "🩺 அறிகுறி பரிசோதனை",
        "tab_hospitals": "🏥 மருத்துவமனை தேடல்",
        "tab_scanner": "📷 மருந்து ஸ்கேனர்",
        "tab_assistant": "🤖 AI உதவியாளர் & நினைவூட்டல்",
        "tab_documents": "📁 என் ஆவணங்கள்",
        "tab_history": "🕑 முந்தைய வரலாறு",
        "describe_symptoms": "உங்கள் அறிகுறிகளை விவரிக்கவும்",
        "age": "வயது",
        "duration_q": "இந்த அறிகுறிகள் எவ்வளவு நாட்களாக உள்ளன?",
        "severity_q": "உங்கள் அறிகுறிகள் எவ்வளவு தீவிரமானவை?",
        "analyze_btn": "🔍 அறிகுறிகளை பகுப்பாய்வு செய்",
        "select_city": "🏙️ நகரத்தை தேர்ந்தெடுக்கவும்",
        "select_region": "🗺️ வருவாய் மாவட்டத்தை தேர்ந்தெடுக்கவும்",
        "select_district": "📍 மாவட்டம் / மண்டலத்தை தேர்ந்தெடுக்கவும்",
        "select_procedure": "🧪 மருத்துவ பரிசோதனையை தேர்ந்தெடுக்கவும்",
        "best_hospital": "🏆 உங்களுக்கான சிறந்த மருத்துவமனை",
        "in_district": "உங்கள் மாவட்டத்தில்",
        "other_district": "இந்த வருவாய் மாவட்டத்தின் மற்ற மண்டலங்கள்",
        "emergency_contact": "அவசர தொடர்பு",
        "upload_docs": "உங்கள் மருத்துவ ஆவணங்களை பதிவேற்றவும்",
        "history_empty": "இதுவரை பரிசோதனை இல்லை.",
        "diet_suggestion_title": "🥗 பொது உணவு பரிந்துரை",
        "location_link_text": "📍 வரைபடத்தில் காண்க",
        "scan_medicine_btn": "🔍 மருந்தை அடையாளம் காணவும்",
        "add_reminder_btn": "➕ நினைவூட்டல் சேர்",
        "ask_assistant_placeholder": "எளிய சுகாதார கேள்வி கேளுங்கள்...",
    },
    "Gujarati": {
        "app_title": "🩺 મેડિસ્કેન AI",
        "app_subtitle": "આરોગ્ય ભાવ પારદર્શિતા અને ટ્રાયેજ",
        "tab_triage": "🩺 લક્ષણ તપાસ",
        "tab_hospitals": "🏥 હોસ્પિટલ શોધો",
        "tab_scanner": "📷 દવા સ્કેનર",
        "tab_assistant": "🤖 AI સહાયક અને રિમાઇન્ડર",
        "tab_documents": "📁 મારા દસ્તાવેજો",
        "tab_history": "🕑 જૂનો ઇતિહાસ",
        "describe_symptoms": "તમારા લક્ષણો વર્ણવો",
        "age": "ઉંમર",
        "duration_q": "તમને આ લક્ષણો કેટલા દિવસથી છે?",
        "severity_q": "તમારા લક્ષણો કેટલા ગંભીર છે?",
        "analyze_btn": "🔍 લક્ષણોનું વિશ્લેષણ કરો",
        "select_city": "🏙️ શહેર પસંદ કરો",
        "select_region": "🗺️ મહેસૂલ જિલ્લો પસંદ કરો",
        "select_district": "📍 જિલ્લો / મંડળ પસંદ કરો",
        "select_procedure": "🧪 મેડિકલ ટેસ્ટ પસંદ કરો",
        "best_hospital": "🏆 તમારા માટે શ્રેષ્ઠ હોસ્પિટલ",
        "in_district": "તમારા જિલ્લામાં",
        "other_district": "આ મહેસૂલ જિલ્લાના અન્ય મંડળો",
        "emergency_contact": "ઇમરજન્સી સંપર્ક",
        "upload_docs": "તમારા મેડિકલ દસ્તાવેજો અપલોડ કરો",
        "history_empty": "હજુ સુધી કોઈ તપાસ નથી.",
        "diet_suggestion_title": "🥗 સામાન્ય આહાર સૂચન",
        "location_link_text": "📍 મેપ પર જુઓ",
        "scan_medicine_btn": "🔍 દવા ઓળખો",
        "add_reminder_btn": "➕ રિમાઇન્ડર ઉમેરો",
        "ask_assistant_placeholder": "એક સરળ સ્વાસ્થ્ય પ્રશ્ન પૂછો...",
    },
    "Urdu": {
        "app_title": "🩺 میڈی سکین اے آئی",
        "app_subtitle": "صحت کی قیمت میں شفافیت اور ٹرائیج",
        "tab_triage": "🩺 علامات کی جانچ",
        "tab_hospitals": "🏥 ہسپتال تلاش کریں",
        "tab_scanner": "📷 دوا اسکینر",
        "tab_assistant": "🤖 اے آئی معاون اور یاد دہانی",
        "tab_documents": "📁 میرے دستاویزات",
        "tab_history": "🕑 پرانی تاریخ",
        "describe_symptoms": "اپنی علامات بیان کریں",
        "age": "عمر",
        "duration_q": "یہ علامات آپ کو کتنے دن سے ہیں؟",
        "severity_q": "آپ کی علامات کتنی شدید ہیں؟",
        "analyze_btn": "🔍 علامات کا تجزیہ کریں",
        "select_city": "🏙️ شہر منتخب کریں",
        "select_region": "🗺️ ریونیو ضلع منتخب کریں",
        "select_district": "📍 ضلع / منڈل منتخب کریں",
        "select_procedure": "🧪 میڈیکل ٹیسٹ منتخب کریں",
        "best_hospital": "🏆 آپ کے لیے بہترین ہسپتال",
        "in_district": "آپ کے ضلع میں",
        "other_district": "اس ریونیو ضلع کے دیگر منڈل",
        "emergency_contact": "ایمرجنسی رابطہ",
        "upload_docs": "اپنے میڈیکل دستاویزات اپلوڈ کریں",
        "history_empty": "ابھی تک کوئی جانچ نہیں۔",
        "diet_suggestion_title": "🥗 عمومی غذائی مشورہ",
        "location_link_text": "📍 نقشے پر دیکھیں",
        "scan_medicine_btn": "🔍 دوا کی شناخت کریں",
        "add_reminder_btn": "➕ یاد دہانی شامل کریں",
        "ask_assistant_placeholder": "ایک سادہ صحت سوال پوچھیں...",
    },
    "Kannada": {
        "app_title": "🩺 ಮೆಡಿಸ್ಕ್ಯಾನ್ AI",
        "app_subtitle": "ಆರೋಗ್ಯ ಬೆಲೆ ಪಾರದರ್ಶಕತೆ ಮತ್ತು ಟ್ರಯಾಜ್",
        "tab_triage": "🩺 ಲಕ್ಷಣ ಪರೀಕ್ಷೆ",
        "tab_hospitals": "🏥 ಆಸ್ಪತ್ರೆ ಹುಡುಕಿ",
        "tab_scanner": "📷 ಔಷಧ ಸ್ಕ್ಯಾನರ್",
        "tab_assistant": "🤖 AI ಸಹಾಯಕ ಮತ್ತು ನೆನಪೋಲೆ",
        "tab_documents": "📁 ನನ್ನ ದಾಖಲೆಗಳು",
        "tab_history": "🕑 ಹಿಂದಿನ ಇತಿಹಾಸ",
        "describe_symptoms": "ನಿಮ್ಮ ಲಕ್ಷಣಗಳನ್ನು ವಿವರಿಸಿ",
        "age": "ವಯಸ್ಸು",
        "duration_q": "ಈ ಲಕ್ಷಣಗಳು ಎಷ್ಟು ದಿನಗಳಿಂದ ಇವೆ?",
        "severity_q": "ನಿಮ್ಮ ಲಕ್ಷಣಗಳು ಎಷ್ಟು ತೀವ್ರವಾಗಿವೆ?",
        "analyze_btn": "🔍 ಲಕ್ಷಣಗಳನ್ನು ವಿಶ್ಲೇಷಿಸಿ",
        "select_city": "🏙️ ನಗರ ಆಯ್ಕೆಮಾಡಿ",
        "select_region": "🗺️ ರೆವೆನ್ಯೂ ಜಿಲ್ಲೆ ಆಯ್ಕೆಮಾಡಿ",
        "select_district": "📍 ಜಿಲ್ಲೆ / ಮಂಡಲ ಆಯ್ಕೆಮಾಡಿ",
        "select_procedure": "🧪 ವೈದ್ಯಕೀಯ ಪರೀಕ್ಷೆ ಆಯ್ಕೆಮಾಡಿ",
        "best_hospital": "🏆 ನಿಮಗಾಗಿ ಉತ್ತಮ ಆಸ್ಪತ್ರೆ",
        "in_district": "ನಿಮ್ಮ ಜಿಲ್ಲೆಯಲ್ಲಿ",
        "other_district": "ಈ ರೆವೆನ್ಯೂ ಜಿಲ್ಲೆಯ ಇತರ ಮಂಡಲಗಳು",
        "emergency_contact": "ತುರ್ತು ಸಂಪರ್ಕ",
        "upload_docs": "ನಿಮ್ಮ ವೈದ್ಯಕೀಯ ದಾಖಲೆಗಳನ್ನು ಅಪ್‌ಲೋಡ್ ಮಾಡಿ",
        "history_empty": "ಇನ್ನೂ ಯಾವುದೇ ಪರೀಕ್ಷೆ ಇಲ್ಲ.",
        "diet_suggestion_title": "🥗 ಸಾಮಾನ್ಯ ಆಹಾರ ಸಲಹೆ",
        "location_link_text": "📍 ನಕ್ಷೆಯಲ್ಲಿ ನೋಡಿ",
        "scan_medicine_btn": "🔍 ಔಷಧವನ್ನು ಗುರುತಿಸಿ",
        "add_reminder_btn": "➕ ನೆನಪೋಲೆ ಸೇರಿಸಿ",
        "ask_assistant_placeholder": "ಸರಳ ಆರೋಗ್ಯ ಪ್ರಶ್ನೆ ಕೇಳಿ...",
    },
    "Odia": {
        "app_title": "🩺 ମେଡିସ୍କାନ୍ AI",
        "app_subtitle": "ସ୍ୱାସ୍ଥ୍ୟ ମୂଲ୍ୟ ସ୍ୱଚ୍ଛତା ଏବଂ ଟ୍ରାଏଜ୍",
        "tab_triage": "🩺 ଲକ୍ଷଣ ପରୀକ୍ଷା",
        "tab_hospitals": "🏥 ଡାକ୍ତରଖାନା ଖୋଜ",
        "tab_scanner": "📷 ଔଷଧ ସ୍କାନର",
        "tab_assistant": "🤖 AI ସହାୟକ ଓ ସ୍ମରଣ",
        "tab_documents": "📁 ମୋର ଡକୁମେଣ୍ଟ",
        "tab_history": "🕑 ପୁରୁଣା ଇତିହାସ",
        "describe_symptoms": "ଆପଣଙ୍କ ଲକ୍ଷଣ ବର୍ଣ୍ଣନା କରନ୍ତୁ",
        "age": "ବୟସ",
        "duration_q": "ଏହି ଲକ୍ଷଣ କେତେ ଦିନ ହେଲା?",
        "severity_q": "ଆପଣଙ୍କ ଲକ୍ଷଣ କେତେ ଗମ୍ଭୀର?",
        "analyze_btn": "🔍 ଲକ୍ଷଣ ବିଶ୍ଳେଷଣ କରନ୍ତୁ",
        "select_city": "🏙️ ସହର ବାଛନ୍ତୁ",
        "select_region": "🗺️ ରେଭିନ୍ୟୁ ଜିଲ୍ଲା ବାଛନ୍ତୁ",
        "select_district": "📍 ଜିଲ୍ଲା / ମଣ୍ଡଳ ବାଛନ୍ତୁ",
        "select_procedure": "🧪 ମେଡିକାଲ ଟେଷ୍ଟ ବାଛନ୍ତୁ",
        "best_hospital": "🏆 ଆପଣଙ୍କ ପାଇଁ ସର୍ବୋତ୍ତମ ଡାକ୍ତରଖାନା",
        "in_district": "ଆପଣଙ୍କ ଜିଲ୍ଲାରେ",
        "other_district": "ଏହି ରେଭିନ୍ୟୁ ଜିଲ୍ଲାର ଅନ୍ୟ ମଣ୍ଡଳ",
        "emergency_contact": "ଜରୁରୀ ସମ୍ପର୍କ",
        "upload_docs": "ଆପଣଙ୍କ ମେଡିକାଲ ଡକୁମେଣ୍ଟ ଅପଲୋଡ କରନ୍ତୁ",
        "history_empty": "ଏପର୍ଯ୍ୟନ୍ତ କୌଣସି ପରୀକ୍ଷା ନାହିଁ।",
        "diet_suggestion_title": "🥗 ସାଧାରଣ ଖାଦ୍ୟ ପରାମର୍ଶ",
        "location_link_text": "📍 ମାନଚିତ୍ରରେ ଦେଖନ୍ତୁ",
        "scan_medicine_btn": "🔍 ଔଷଧ ଚିହ୍ନଟ କରନ୍ତୁ",
        "add_reminder_btn": "➕ ସ୍ମରଣ ଯୋଡନ୍ତୁ",
        "ask_assistant_placeholder": "ଏକ ସରଳ ସ୍ୱାସ୍ଥ୍ୟ ପ୍ରଶ୍ନ ପଚାରନ୍ତୁ...",
    },
    "Malayalam": {
        "app_title": "🩺 മെഡിസ്കാൻ AI",
        "app_subtitle": "ആരോഗ്യ വില സുതാര്യതയും ട്രയാജും",
        "tab_triage": "🩺 രോഗലക്ഷണ പരിശോധന",
        "tab_hospitals": "🏥 ആശുപത്രി കണ്ടെത്തുക",
        "tab_scanner": "📷 മരുന്ന് സ്കാനർ",
        "tab_assistant": "🤖 AI സഹായി & ഓർമ്മപ്പെടുത്തൽ",
        "tab_documents": "📁 എൻ്റെ രേഖകൾ",
        "tab_history": "🕑 മുൻ ചരിത്രം",
        "describe_symptoms": "നിങ്ങളുടെ ലക്ഷണങ്ങൾ വിവരിക്കുക",
        "age": "വയസ്സ്",
        "duration_q": "ഈ ലക്ഷണങ്ങൾ എത്ര ദിവസമായി ഉണ്ട്?",
        "severity_q": "നിങ്ങളുടെ ലക്ഷണങ്ങൾ എത്ര ഗുരുതരമാണ്?",
        "analyze_btn": "🔍 ലക്ഷണങ്ങൾ വിശകലനം ചെയ്യുക",
        "select_city": "🏙️ നഗരം തിരഞ്ഞെടുക്കുക",
        "select_region": "🗺️ റെവന്യൂ ജില്ല തിരഞ്ഞെടുക്കുക",
        "select_district": "📍 ജില്ല / മണ്ഡലം തിരഞ്ഞെടുക്കുക",
        "select_procedure": "🧪 മെഡിക്കൽ ടെസ്റ്റ് തിരഞ്ഞെടുക്കുക",
        "best_hospital": "🏆 നിങ്ങൾക്കായുള്ള മികച്ച ആശുപത്രി",
        "in_district": "നിങ്ങളുടെ ജില്ലയിൽ",
        "other_district": "ഈ റെവന്യൂ ജില്ലയിലെ മറ്റ് മണ്ഡലങ്ങൾ",
        "emergency_contact": "അടിയന്തര ബന്ധപ്പെടൽ",
        "upload_docs": "നിങ്ങളുടെ മെഡിക്കൽ രേഖകൾ അപ്‌ലോഡ് ചെയ്യുക",
        "history_empty": "ഇതുവരെ പരിശോധനകൾ ഇല്ല.",
        "diet_suggestion_title": "🥗 പൊതു ഭക്ഷണ നിർദ്ദേശം",
        "location_link_text": "📍 മാപ്പിൽ കാണുക",
        "scan_medicine_btn": "🔍 മരുന്ന് തിരിച്ചറിയുക",
        "add_reminder_btn": "➕ ഓർമ്മപ്പെടുത്തൽ ചേർക്കുക",
        "ask_assistant_placeholder": "ലളിതമായ ആരോഗ്യ ചോദ്യം ചോദിക്കുക...",
    },
    "Punjabi": {
        "app_title": "🩺 ਮੈਡੀਸਕੈਨ AI",
        "app_subtitle": "ਸਿਹਤ ਕੀਮਤ ਪਾਰਦਰਸ਼ਤਾ ਅਤੇ ਟ੍ਰਾਈਏਜ",
        "tab_triage": "🩺 ਲੱਛਣ ਜਾਂਚ",
        "tab_hospitals": "🏥 ਹਸਪਤਾਲ ਲੱਭੋ",
        "tab_scanner": "📷 ਦਵਾਈ ਸਕੈਨਰ",
        "tab_assistant": "🤖 AI ਸਹਾਇਕ ਅਤੇ ਯਾਦ ਦਹਾਨੀ",
        "tab_documents": "📁 ਮੇਰੇ ਦਸਤਾਵੇਜ਼",
        "tab_history": "🕑 ਪੁਰਾਣਾ ਇਤਿਹਾਸ",
        "describe_symptoms": "ਆਪਣੇ ਲੱਛਣ ਦੱਸੋ",
        "age": "ਉਮਰ",
        "duration_q": "ਇਹ ਲੱਛਣ ਤੁਹਾਨੂੰ ਕਿੰਨੇ ਦਿਨਾਂ ਤੋਂ ਹਨ?",
        "severity_q": "ਤੁਹਾਡੇ ਲੱਛਣ ਕਿੰਨੇ ਗੰਭੀਰ ਹਨ?",
        "analyze_btn": "🔍 ਲੱਛਣਾਂ ਦਾ ਵਿਸ਼ਲੇਸ਼ਣ ਕਰੋ",
        "select_city": "🏙️ ਸ਼ਹਿਰ ਚੁਣੋ",
        "select_region": "🗺️ ਰੈਵੇਨਿਊ ਜ਼ਿਲ੍ਹਾ ਚੁਣੋ",
        "select_district": "📍 ਜ਼ਿਲ੍ਹਾ / ਮੰਡਲ ਚੁਣੋ",
        "select_procedure": "🧪 ਮੈਡੀਕਲ ਟੈਸਟ ਚੁਣੋ",
        "best_hospital": "🏆 ਤੁਹਾਡੇ ਲਈ ਸਭ ਤੋਂ ਵਧੀਆ ਹਸਪਤਾਲ",
        "in_district": "ਤੁਹਾਡੇ ਜ਼ਿਲ੍ਹੇ ਵਿੱਚ",
        "other_district": "ਇਸ ਰੈਵੇਨਿਊ ਜ਼ਿਲ੍ਹੇ ਦੇ ਹੋਰ ਮੰਡਲ",
        "emergency_contact": "ਐਮਰਜੈਂਸੀ ਸੰਪਰਕ",
        "upload_docs": "ਆਪਣੇ ਮੈਡੀਕਲ ਦਸਤਾਵੇਜ਼ ਅੱਪਲੋਡ ਕਰੋ",
        "history_empty": "ਹਾਲੇ ਕੋਈ ਜਾਂਚ ਨਹੀਂ।",
        "diet_suggestion_title": "🥗 ਆਮ ਖੁਰਾਕ ਸੁਝਾਅ",
        "location_link_text": "📍 ਨਕਸ਼ੇ 'ਤੇ ਦੇਖੋ",
        "scan_medicine_btn": "🔍 ਦਵਾਈ ਪਛਾਣੋ",
        "add_reminder_btn": "➕ ਯਾਦ ਦਹਾਨੀ ਸ਼ਾਮਲ ਕਰੋ",
        "ask_assistant_placeholder": "ਇੱਕ ਸਧਾਰਨ ਸਿਹਤ ਸਵਾਲ ਪੁੱਛੋ...",
    },
}

# ============================================================
# General diet suggestions (only shown for a few recognized
# keywords — NOT a personalized nutrition/medical plan)
# ============================================================

DIET_SUGGESTIONS = {
    "fever": "Drink plenty of fluids (water, ORS, coconut water). Prefer light, "
             "easy-to-digest food like khichdi or soup. Avoid oily and heavy food "
             "until the fever settles.",
    "diabetes": "Prefer whole grains over refined flour, include more vegetables "
                "and fiber, and avoid sugary drinks and sweets. Try to space out "
                "meals evenly through the day.",
    "sugar": "Prefer whole grains over refined flour, include more vegetables "
             "and fiber, and avoid sugary drinks and sweets.",
    "acidity": "Avoid spicy, oily, and caffeinated food. Eat smaller, more "
               "frequent meals, and avoid lying down immediately after eating.",
    "gastric": "Avoid spicy, oily, and caffeinated food. Eat smaller, more "
               "frequent meals, and avoid lying down immediately after eating.",
    "cold": "Warm fluids like soup and herbal tea can help. Avoid cold drinks "
            "and ice cream while symptomatic.",
    "cough": "Warm water with honey (for adults) can soothe the throat. Avoid "
             "cold and fried foods.",
    "diarrhea": "Focus on ORS and fluids to prevent dehydration. Bland foods "
                "like rice, banana, and toast are easier to digest. Avoid dairy "
                "and spicy food temporarily.",
    "vomiting": "Sip small amounts of ORS or water frequently rather than "
                "drinking a lot at once. Avoid solid or spicy food until "
                "vomiting settles.",
}

DIET_DISCLAIMER = (
    "This is general wellness information only, not a personalized medical "
    "or nutrition plan. For an actual diet plan, especially with any "
    "existing medical condition, please consult a registered dietitian or "
    "your doctor."
)

# ============================================================
# Helper: build a Google Maps search link for a hospital
# ------------------------------------------------------------
# We don't have real GPS coordinates for these sample hospitals,
# so this opens a Maps *search* for the hospital name + area —
# not a pin baked into real coordinates.
# ============================================================

def get_maps_link(hospital_name: str, area: str, city: str) -> str:
    query = f"{hospital_name} {area} {city} India"
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"

# ============================================================
# Helper: look up a medicine by (OCR or typed) text
# ============================================================

def find_medicine(query_text: str, medicines_df: pd.DataFrame):
    if not query_text or not query_text.strip():
        return None

    query_lower = query_text.lower()
    med_names = medicines_df["name"].tolist()

    # 1) Direct substring match — most reliable when it happens
    for name in med_names:
        if name.lower() in query_lower:
            return medicines_df[medicines_df["name"] == name].iloc[0]

    # 2) Fuzzy match, line by line (handles messy OCR text)
    lines = [l.strip() for l in query_text.split("\n") if l.strip()] or [query_text]
    best_match = None
    best_ratio = 0.0

    for line in lines:
        close = difflib.get_close_matches(
            line.lower(), [n.lower() for n in med_names], n=1, cutoff=0.5
        )
        if close:
            idx = [n.lower() for n in med_names].index(close[0])
            candidate = med_names[idx]
            ratio = difflib.SequenceMatcher(None, line.lower(), close[0]).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

    if best_match:
        return medicines_df[medicines_df["name"] == best_match].iloc[0]

    return None

# ============================================================
# Google-like medicine lookup helpers
# ------------------------------------------------------------
# The demo CSV only knows ~35 medicines, so a typed name is also looked up in
# public drug databases (openFDA, RxNorm, Wikipedia).  Everything below renders
# that result; the lookup itself lives in modules/medicine_search.py.
# ============================================================

MEDICINE_FIELD_LABELS = (
    ("generic_name", "Generic name"),
    ("substance", "Active substance"),
    ("form", "Form"),
    ("route", "How it's taken"),
    ("manufacturer", "Manufacturer"),
    ("term_type", "Matched as"),
)


def medicine_detail_rows(result):
    """Build the (label, value) pairs that actually have something to show."""
    rows = []
    if result.get("purpose"):
        rows.append(("Purpose", result["purpose"]))
    if result.get("uses"):
        rows.append(("What it's used for", result["uses"]))
    if result.get("active_ingredient"):
        rows.append(("Active ingredient", result["active_ingredient"]))
    for key, label in MEDICINE_FIELD_LABELS:
        value = result.get(key)
        if value:
            rows.append((label, value))
    if result.get("related_names"):
        rows.append(("Also known as", ", ".join(result["related_names"][:6])))
    if result.get("warnings"):
        rows.append(("Warnings", result["warnings"]))
    if result.get("side_effects"):
        rows.append(("Side effects", result["side_effects"]))
    if result.get("precautions"):
        rows.append(("Precautions", result["precautions"]))
    if result.get("interactions"):
        rows.append(("Interactions", result["interactions"]))
    return rows


def render_medicine_web_result(result, query):
    """Show one searched medicine: facts, sources, and web links."""
    if not result:
        return

    if not result.get("matched"):
        st.markdown(
            f"""
            <div class="ms-web-result">
                <div class="ms-medicine-title">
                    <span class="ms-medicine-icon">?</span>
                    <div>
                        <span>No database match</span>
                        <h2>{safe_text(query)}</h2>
                    </div>
                </div>
                <p class="ms-web-note">
                    None of the public medicine databases recognise this exact
                    name. It may be an Indian brand, a local formulation, or a
                    spelling mistake — use the web links below to check, or ask
                    the AI Assistant.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        confidence = result.get("confidence")
        banner = ""
        if confidence == "partial":
            banner = (
                "<p class='ms-web-note ms-web-note-warn'>Closest database match, "
                "not an exact name match — check that this is really the "
                "medicine you searched for.</p>"
            )
        image = ""
        if result.get("image_url"):
            image = (
                f"<img class='ms-web-image' src=\"{safe_text(result['image_url'])}\" "
                f"alt=\"{safe_text(result.get('wiki_title') or query)}\" />"
            )
        rows = "".join(
            f"<div><b>{safe_text(label)}</b><span>{safe_text(value)}</span></div>"
            for label, value in medicine_detail_rows(result)
        )
        source_line = " + ".join(result.get("sources") or [])
        summary_html = ""
        if result.get("description"):
            summary_html = (
                "<div class='ms-web-summary'>"
                + safe_text(result["description"])
                + "</div>"
            )
        st.markdown(
            f"""
            <div class="ms-web-result">
                <div class="ms-medicine-title">
                    <span class="ms-medicine-icon">M</span>
                    <div>
                        <span>Medicine information from {safe_text(source_line)}</span>
                        <h2>{safe_text(result.get('display_name') or query)}</h2>
                    </div>
                    {image}
                </div>
                {banner}
                {summary_html}
                <div class="ms-medicine-grid">{rows}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.caption(
        "Reference information from openFDA (US drug labels), RxNorm (NIH), and "
        "Wikipedia. It is general information, not advice about your own "
        "treatment, and it never includes a dosage for you to follow."
    )

    # The "search it anywhere" links — this is what makes it behave like a
    # general web search rather than a closed demo database.
    st.markdown("**🌐 Search this medicine anywhere**")
    for link in result.get("web_links") or []:
        st.markdown(
            f"""
            <a class="ms-web-link" href="{safe_text(link['url'])}" target="_blank"
               rel="noopener noreferrer">
                <strong>{safe_text(link['label'])}</strong>
                <span>{safe_text(link['hint'])}</span>
            </a>
            """,
            unsafe_allow_html=True,
        )


def render_medicine_ai_summary(result, query):
    """Ask the assistant to explain the searched medicine in the user's language.

    This is what covers the names no public database has (many Indian brands)
    and it reuses the same reply-language switch as the AI Assistant, so the
    explanation comes back in English, Hindi, or Telugu.
    """
    language_name = st.session_state.assistant_language
    native = ASSISTANT_LANGUAGES[language_name]["native"]
    assistant_label = assistant_ui(language_name)["assistant"]

    st.markdown("---")
    st.markdown(f"**🤖 Explain this medicine in {native}**")

    if not GROQ_LIB_AVAILABLE or GROQ_API_KEY is None:
        st.caption(
            "Add a Groq API key to get a plain-language explanation in your "
            "language."
        )
        return

    cached = st.session_state.get("medicine_ai_summary")
    if cached and cached.get("query") == query and cached.get("language") == language_name:
        st.markdown(
            f"""
            <div class="ai-message ai-assistant-message">
                <div class="ai-message-label">{safe_text(assistant_label)}</div>
                <div>{safe_text(cached["text"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    if st.button(f"Explain in {native}", key="medicine_explain_btn", type="primary"):
        summary_loading = st.empty()
        summary_loading.markdown(
            f"""
            <div class="ms-inline-loader ms-ai-loader">
                <div class="ms-loading-content">
                    <div class="ms-spinner"></div>
                    <p>{safe_text(assistant_ui(language_name)["thinking"])}</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        summary = ""
        try:
            summary = ask_groq(
                messages=[
                    {
                        "role": "system",
                        "content": ASSISTANT_SAFETY_RULES,
                    },
                    {
                        "role": "user",
                        "content": build_medicine_prompt(query, result, language_name),
                    },
                ],
                max_tokens=320,
                temperature=0.3,
            )
        except Exception as exc:
            summary_loading.empty()
            st.error(f"Could not generate the explanation: {exc}")
        summary_loading.empty()
        if summary:
            st.session_state.medicine_ai_summary = {
                "query": query,
                "language": language_name,
                "text": summary,
            }
            st.rerun()

# ============================================================
# Session state (feature data — separate from auth state above)
# ============================================================

if "documents" not in st.session_state:
    st.session_state.documents = []           # uploaded document metadata (from the local database)

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []        # AI assistant conversation

if "ai_last_error" not in st.session_state:
    st.session_state.ai_last_error = ""        # last Groq failure, for display

if "chat_save_error" not in st.session_state:
    st.session_state.chat_save_error = ""      # last chat storage failure

if "ai_last_reply_len" not in st.session_state:
    st.session_state.ai_last_reply_len = 0     # size of the last model reply

if "ai_last_stage" not in st.session_state:
    st.session_state.ai_last_stage = "idle"   # how far the last request got

# Result of the live Groq reachability check shown on the assistant screen.
# Kept so the check costs one request per session instead of one per rerun.
if "groq_check" not in st.session_state:
    st.session_state.groq_check = None        # (ok, message) from groq_status()

if "groq_check_failed" not in st.session_state:
    st.session_state.groq_check_failed = False

if "groq_check_forced" not in st.session_state:
    st.session_state.groq_check_forced = False

if "chat_pending" not in st.session_state:
    st.session_state.chat_pending = []         # messages the database rejected

# Reply language for the AI assistant.  It follows the sidebar language on the
# first visit and can then be switched on its own from the assistant screen.
if "assistant_language" not in st.session_state:
    st.session_state.assistant_language = "English"

if "assistant_language_manual" not in st.session_state:
    # False while the assistant still mirrors the sidebar language.
    st.session_state.assistant_language_manual = False

if "medicine_lookup" not in st.session_state:
    # {"query", "language", "result"} for the last typed-medicine web search.
    st.session_state.medicine_lookup = None

if "medicine_ai_summary" not in st.session_state:
    # Cached AI summary so switching the reply language does not re-query.
    st.session_state.medicine_ai_summary = None

if "reminders" not in st.session_state:
    st.session_state.reminders = []            # medication reminders

if "active_feature" not in st.session_state:
    st.session_state.active_feature = "Home"

if "activity_log" not in st.session_state:
    st.session_state.activity_log = []

if "saved_medicines" not in st.session_state:
    st.session_state.saved_medicines = []

if "notifications" not in st.session_state:
    st.session_state.notifications = []

if "accessibility_large_text" not in st.session_state:
    st.session_state.accessibility_large_text = False

if "accessibility_high_contrast" not in st.session_state:
    st.session_state.accessibility_high_contrast = False


def _format_stored_timestamp(value):
    """Show a stored upload time the same way the upload itself records it."""
    if not value:
        return ""
    try:
        return datetime.datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


def load_local_user_data():
    """Load persistent records from the local SQLite database once per visitor.

    Each group is loaded independently, so one unreadable or missing table
    reports itself instead of blanking out the visitor's other records.
    """
    if not st.session_state.user_id:
        return

    user_id = st.session_state.user_id

    def query(sql, params, label, default):
        conn = get_connection()
        try:
            return conn.execute(sql, params).fetchall()
        except Exception as exc:
            st.warning(f"Could not load your {label}: {exc}")
            return default
        finally:
            conn.close()

    # Load emergency contact
    contact = query(
        "SELECT name, phone, relation FROM emergency_contacts WHERE user_id = ?",
        (user_id,),
        "emergency contact",
        [],
    )
    contact = contact[0] if contact else None
    st.session_state.emergency_contact = (
        {
            "name": contact["name"],
            "phone": contact["phone"],
            "relation": contact["relation"],
        }
        if contact
        else {"name": "", "phone": "", "relation": ""}
    )

    # Load documents
    docs = query(
        "SELECT id, filename, file_type, size_bytes, uploaded_at FROM documents WHERE user_id = ? ORDER BY uploaded_at DESC",
        (user_id,),
        "documents",
        [],
    )
    st.session_state.documents = [
        {
            "id": doc["id"],
            "name": doc["filename"],
            "file_type": doc["file_type"],
            "size_kb": round(float(doc["size_bytes"] or 0) / 1024, 1),
            "uploaded_on": _format_stored_timestamp(doc["uploaded_at"]),
        }
        for doc in docs
    ]

    # Load chat history
    chats = query(
        # created_at only has one-second resolution, so a question and its reply
        # saved in the same second would tie; id breaks the tie into send order.
        "SELECT role, message FROM chat_history WHERE user_id = ? ORDER BY created_at ASC, id ASC",
        (user_id,),
        "chat history",
        [],
    )
    # This reloads on every rerun, so it is also what decides whether a reply
    # the user just received stays on screen. `if chat["message"]` used to drop
    # empty rows, which silently erased a blank model reply instead of showing
    # it — keep the row whenever there is a role, and let the render handle it.
    st.session_state.chat_messages = [
        {"role": chat["role"], "content": chat["message"] or ""}
        for chat in chats
        if chat["role"]
    ]
    # Re-apply anything the database refused to store, so a message the user
    # has already seen is never dropped by the reload above.
    if st.session_state.chat_pending:
        st.session_state.chat_messages = (
            st.session_state.chat_messages + st.session_state.chat_pending
        )

    # Load reminders
    reminders = query(
        "SELECT id, medicine_name, form, reminder_time, food_timing, notes FROM reminders WHERE user_id = ? ORDER BY reminder_time ASC",
        (user_id,),
        "reminders",
        [],
    )
    st.session_state.reminders = [
        {
            "name": rem["medicine_name"],
            "form": rem["form"],
            "food": rem["food_timing"],
            "time": datetime.datetime.strptime(rem["reminder_time"], "%H:%M:%S").time() if rem["reminder_time"] else datetime.time(9, 0),
            "notes": rem["notes"],
            "id": rem["id"],
        }
        for rem in reminders
    ]

    # Load saved medicines
    saved = query(
        "SELECT medicine_name FROM saved_medicines WHERE user_id = ? ORDER BY created_at ASC",
        (user_id,),
        "saved medicines",
        [],
    )
    st.session_state.saved_medicines = [s["medicine_name"] for s in saved]


# ============================================================
# Local SQLite writes
# ------------------------------------------------------------
# These are the write half of load_local_user_data above: anything read
# back on the next visit has to be written here, or it is lost on reload.
# Each helper reports its own failure and returns None, so a write problem
# degrades to a warning instead of taking the whole page down.
# ============================================================

def _write(sql, params, label):
    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.lastrowid
    except Exception as exc:
        st.warning(f"Could not save your {label}: {exc}")
        return None
    finally:
        conn.close()


def save_chat_message(role, content):
    row_id = _write(
        "INSERT INTO chat_history (user_id, role, message) VALUES (?, ?, ?)",
        (st.session_state.user_id, role, content),
        "chat message",
    )
    if row_id is None:
        # The chat path ends in st.rerun(), which rebuilds the conversation from
        # the database. A row that failed to store would therefore vanish from
        # screen with no trace, so hold anything unsaved in session_state and
        # show the reason.
        st.session_state.chat_save_error = (
            f"Could not save the {role} message to the database."
        )
        st.session_state.chat_pending = st.session_state.chat_pending + [
            {"role": role, "content": content}
        ]
    else:
        st.session_state.chat_save_error = ""
        # Now that it is stored, drop the in-memory copy so a later reload does
        # not show the same message twice.
        st.session_state.chat_pending = [
            msg
            for msg in st.session_state.chat_pending
            if not (msg["role"] == role and msg["content"] == content)
        ]
    return row_id


def update_last_chat_message(content):
    """Replace the newest stored message, used when a reply is translated."""
    _write(
        """UPDATE chat_history SET message = ?
           WHERE id = (SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT 1)""",
        (content, st.session_state.user_id),
        "chat message",
    )


def delete_last_chat_message():
    _write(
        """DELETE FROM chat_history
           WHERE id = (SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT 1)""",
        (st.session_state.user_id,),
        "chat message",
    )


def clear_stored_chat():
    _write(
        "DELETE FROM chat_history WHERE user_id = ?",
        (st.session_state.user_id,),
        "chat history",
    )


def save_reminder(name, form, food, reminder_time, notes):
    return _write(
        """INSERT INTO reminders
           (user_id, medicine_name, form, food_timing, reminder_time, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (st.session_state.user_id, name, form, food, reminder_time, notes),
        "reminder",
    )


def delete_all_reminders():
    _write(
        "DELETE FROM reminders WHERE user_id = ?",
        (st.session_state.user_id,),
        "reminders",
    )


def save_document(filename, file_type, size_bytes):
    return _write(
        """INSERT INTO documents (user_id, filename, file_type, file_path, size_bytes)
           VALUES (?, ?, ?, ?, ?)""",
        # No Storage bucket behind this app, so there is no remote path to keep.
        (st.session_state.user_id, filename, file_type, None, size_bytes),
        "document",
    )


def clear_stored_documents():
    _write(
        "DELETE FROM documents WHERE user_id = ?",
        (st.session_state.user_id,),
        "documents",
    )


# Load local user data
load_local_user_data()


def log_activity(title, kind="Activity", detail=""):
    event = {
        "title": title,
        "kind": kind,
        "detail": detail,
        "time": datetime.datetime.now().strftime("%b %d, %Y %I:%M %p")
    }
    st.session_state.activity_log.insert(0, event)
    st.session_state.activity_log = st.session_state.activity_log[:30]


def add_notification(message, kind="info"):
    st.session_state.notifications.insert(0, {
        "message": message,
        "kind": kind,
        "time": datetime.datetime.now().strftime("%b %d, %I:%M %p")
    })
    st.session_state.notifications = st.session_state.notifications[:20]


def get_recent_triage(limit=5):
    try:
        conn = get_connection()
        df = pd.read_sql_query(
            """SELECT created_at, symptoms, predicted_urgency FROM triage_history
               WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
            conn, params=(st.session_state.user_id, limit)
        )
        conn.close()
        return df
    except Exception:
        return pd.DataFrame()


def safe_text(value):
    """Escape user/database/model text before inserting it into raw HTML."""
    return escape(str(value), quote=True)


def logout_user():
    drop_persist_token()
    clear_local_session_state()
    st.session_state.user_id = None
    st.session_state.user_name = None
    st.session_state.username = None
    st.session_state.emergency_contact = {"name": "", "phone": "", "relation": ""}
    st.session_state.documents = []
    st.session_state.chat_messages = []
    st.session_state.reminders = []
    st.session_state.activity_log = []
    st.session_state.saved_medicines = []
    st.session_state.active_feature = "Home"


# ============================================================
# Sidebar — Account, Language, Emergency Contact
# ============================================================

with st.sidebar:

    # -------------------- Brand & Navigation --------------------
    st.markdown(
        """<div class='ms-sidebar-brand'>
            <div class='ms-sidebar-logo'>✚</div>
            <div><strong>MediScan AI</strong><span>Smarter Healthcare for Everyone</span></div>
        </div>
        <div class='ms-sidebar-search'>🔍&nbsp;&nbsp; Search...</div>""",
        unsafe_allow_html=True
    )

    st.markdown(
        """<div class='ms-live-status'><span class='ms-status-pulse'></span><span>AI services ready</span><span class='ms-status-dots'><i></i><i></i><i></i></span></div>""",
        unsafe_allow_html=True
    )

    nav_items = [
        ("🏠", "Home"), ("🔍", "Search"), ("🩺", "Triage"), ("💊", "Medicine Scanner"), ("🤖", "AI Assistant"),
        ("🏥", "Hospital Finder"), ("⏰", "Reminders"), ("📋", "History"), ("📁", "Documents"),
        ("👤", "Profile"), ("🔔", "Notifications"), ("🔐", "Privacy & Security"), ("♿", "Accessibility")
    ]

    for icon, page_name in nav_items:
        is_active = page_name == st.session_state.get("active_feature", "Home")
        if st.button(
            f"{icon}  {page_name}",
            key=f"nav_{page_name}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
        ):
            st.session_state.active_feature = page_name
            save_persist_ui_state()
            st.rerun()

    st.markdown("""<div class='ms-sidebar-emergency'><div class='ms-emergency-icon'>🚑</div><div><strong>Emergency</strong><span>Call 108</span></div></div>""", unsafe_allow_html=True)

    st.divider()

    # -------------------- Account --------------------
    st.header("Account")
    st.write(f"Logged in as **{st.session_state.user_name}**")

    if st.button("Logout"):
        logout_user()
        st.rerun()

    with st.expander("Account Settings"):

        st.caption(f"Current username: {st.session_state.username}")

        new_username = st.text_input("New Username", key="new_username")
        if st.button("Update Username", key="update_username"):
            cleaned = new_username.strip().lower()
            if not cleaned:
                st.error("Username cannot be empty.")
            elif not re.fullmatch(r"[a-z0-9_]{3,30}", cleaned):
                st.error(
                    "Username must be 3–30 characters using letters, numbers, or underscores."
                )
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("UPDATE users SET username = ? WHERE id = ?", (cleaned, st.session_state.user_id))
                    conn.commit()
                    st.session_state.username = cleaned
                    st.success("Username updated successfully!")
                except sqlite3.IntegrityError:
                    st.error("That username is already taken.")
                finally:
                    conn.close()

        st.divider()

        new_email = st.text_input("New Email", key="new_email")
        if st.button("Update Email", key="update_email"):
            cleaned = new_email.strip().lower()
            if not cleaned:
                st.error("Email cannot be empty.")
            elif "@" not in cleaned:
                st.error("Please enter a valid email address.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("UPDATE users SET email = ? WHERE id = ?", (cleaned, st.session_state.user_id))
                    conn.commit()
                    st.success("✅ Email updated successfully!")
                except sqlite3.IntegrityError:
                    st.error("❌ That email is already registered.")
                finally:
                    conn.close()

        st.divider()

        current_password = st.text_input("Current Password", type="password", key="current_password")
        new_password = st.text_input("New Password", type="password", key="new_password")
        confirm_password = st.text_input("Confirm New Password", type="password", key="confirm_password")

        if st.button("Update Password", key="update_password"):
            if not current_password:
                st.error("Please enter your current password.")
            elif not new_password:
                st.error("Please enter a new password.")
            elif new_password != confirm_password:
                st.error("New passwords do not match.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT password_hash FROM users WHERE id = ?", (st.session_state.user_id,))
                user = cursor.fetchone()

                if user and verify_password(current_password, user["password_hash"]):
                    cursor.execute(
                        "UPDATE users SET password_hash = ? WHERE id = ?",
                        (hash_password(new_password), st.session_state.user_id)
                    )
                    conn.commit()
                    st.success("✅ Password updated successfully!")
                else:
                    st.error("❌ Current password is incorrect.")
                conn.close()

    st.divider()

    # -------------------- Language --------------------
    language = st.selectbox("Language", list(TRANSLATIONS.keys()), key="language_select")
    T = TRANSLATIONS[language]

    # Follow the app language with the assistant's reply language until the
    # user picks a language on the assistant screen itself, after which their
    # own choice wins.
    if not st.session_state.get("assistant_language_manual"):
        st.session_state.assistant_language = assistant_language_name(language)

    st.divider()

    # -------------------- Emergency Contact --------------------
    st.subheader(T["emergency_contact"].replace("", ""))

    with st.form("emergency_contact_form"):
        ec_name = st.text_input(
            "Contact Name",
            value=st.session_state.emergency_contact["name"],
            key="emergency_contact_name",
        )
        ec_phone = st.text_input(
            "Contact Phone",
            value=st.session_state.emergency_contact["phone"],
            key="emergency_contact_phone",
        )
        ec_relation = st.text_input(
            "Relation",
            value=st.session_state.emergency_contact["relation"],
            key="emergency_contact_relation",
        )
        saved = st.form_submit_button("Save Contact")

        if saved:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO emergency_contacts (user_id, name, phone, relation)
                VALUES (?, ?, ?, ?)
                """,
                (st.session_state.user_id, ec_name, ec_phone, ec_relation)
            )
            conn.commit()
            conn.close()
            st.session_state.emergency_contact = {"name": ec_name, "phone": ec_phone, "relation": ec_relation}
            st.success("Emergency contact saved successfully!")

    if st.session_state.emergency_contact["name"]:
        contact_name = safe_text(st.session_state.emergency_contact["name"])
        contact_relation = safe_text(st.session_state.emergency_contact["relation"])
        contact_phone_display = safe_text(st.session_state.emergency_contact["phone"])
        contact_phone_href = quote(
            str(st.session_state.emergency_contact["phone"]).strip(), safe="+"
        )
        st.markdown(
            f"""
            <div class="emergency-box">
            <b>Your contact:</b> {contact_name}
            ({contact_relation})<br>
            <a href="tel:{contact_phone_href}">{contact_phone_display}</a>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.markdown("**National Emergency Numbers (India)**")
    st.markdown(
        """
        <div class="emergency-box">
        Ambulance: <a href="tel:108">108</a><br>
        Police: <a href="tel:100">100</a><br>
        National Emergency: <a href="tel:112">112</a><br>
        Women Helpline: <a href="tel:1091">1091</a>
        </div>
        """,
        unsafe_allow_html=True
    )

# ============================================================
# Load trained triage model
# ============================================================

# Only the Triage page uses this model, so a load failure must not end the
# script: st.stop() here would blank every page, including the AI Assistant,
# and the real cause (a scikit-learn version that cannot unpickle the saved
# pipeline) is easy to miss. Report it and carry on with model = None.
try:
    model = joblib.load(MODEL_PATH)
except Exception as e:
    model = None
    st.error(
        f"Unable to load the triage model: {e}. The Triage page is unavailable; "
        "every other page still works. Retrain it with "
        "`python modules/train_model.py` if the saved model is out of date."
    )

# ============================================================
# Header
# ============================================================

if st.session_state.get("active_feature", "Home") != "Home":
    st.markdown(
        f"""<div class='main-header'><h1>{T['app_title']}</h1><p>{T['app_subtitle']}</p></div>""",
        unsafe_allow_html=True
    )
    st.info("MediScan AI is an educational prototype and does not provide medical diagnosis or replace professional medical advice.")


# ============================================================
# HOME DASHBOARD — reference-inspired visual dashboard
# ============================================================
if st.session_state.get("active_feature", "Home") == "Home":
    hour = datetime.datetime.now().hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")

    recent_triage = get_recent_triage(3)
    reminder_count = len(st.session_state.reminders)
    document_count = len(st.session_state.documents)
    recent_count = len(recent_triage) + len(st.session_state.activity_log)
    status = recent_triage.iloc[0]["predicted_urgency"] if not recent_triage.empty else "No recent check"

    st.markdown(
        f"""<div class='ms-dashboard-welcome'>
            <div class='ms-dashboard-copy'>
                <div class='ms-eyebrow'>SMARTER HEALTHCARE</div>
                <h2>{greeting}, {safe_text(st.session_state.user_name)} <span>👋</span></h2>
                <p>Your AI-powered healthcare companion</p>
            </div>
            <div class='ms-dashboard-live'><span class='ms-status-pulse'></span> AI services ready</div>
        </div>
        <div class='ms-home-hero'>
            <div class='ms-home-hero-copy'>
                <div class='ms-eyebrow'>MEDISCAN AI</div>
                <h1>Smarter Healthcare<br><span>for a Healthier Tomorrow</span></h1>
                <p>Use AI to understand your symptoms, scan medicines, find hospitals, get insights and manage your health — all in one place.</p>
            </div>
            <div class='ms-home-doctor'></div>
            <div class='ms-home-trust'><b>✓</b><div><strong>Trusted</strong><br><span>AI Healthcare Support</span></div></div>
        </div>""",
        unsafe_allow_html=True
    )

    hero_cta_1, hero_cta_2 = st.columns(2, gap="medium")
    with hero_cta_1:
        if st.button("Start Health Check →", key="hero_cta_start", use_container_width=True):
            st.session_state.active_feature = "Triage"
            st.rerun()
    with hero_cta_2:
        if st.button("Explore Hospital Finder", key="hero_cta_hospitals", use_container_width=True):
            st.session_state.active_feature = "Hospital Finder"
            st.rerun()

    st.markdown("<div class='ms-section-title'><h3>How can we help you today?</h3><p>Choose a health tool to get started.</p></div>", unsafe_allow_html=True)

    actions = [
        ("🩺", "Symptom Triage", "Check symptoms with AI", "Triage", "blue"),
        ("💊", "Medicine Scanner", "Scan & identify medicines", "Medicine Scanner", "coral"),
        ("🤖", "AI Assistant", "Ask health-related questions", "AI Assistant", "cyan"),
        ("🏥", "Hospital Finder", "Find nearby hospitals", "Hospital Finder", "green"),
        ("⏰", "Reminders", "Manage your reminders", "Reminders", "amber"),
        ("📋", "History", "View past records", "History", "violet"),
    ]
    cols = st.columns(3, gap="medium")
    for i, (icon, title, subtitle, target, tone) in enumerate(actions):
        with cols[i % 3]:
            st.markdown(
                f"""<div class='ms-home-feature {tone}'>
                    <div class='ms-home-feature-icon'>{icon}</div>
                    <div class='ms-home-feature-copy'><strong>{title}</strong><span>{subtitle}</span></div>
                    <span class='ms-home-feature-arrow'>›</span>
                </div>""",
                unsafe_allow_html=True
            )
            if st.button(f"Open {title}", key=f"dash_action_{i}", use_container_width=True):
                st.session_state.active_feature = target
                st.rerun()

    st.markdown("<div class='ms-section-title ms-home-section-gap'><h3>Today's Health</h3></div>", unsafe_allow_html=True)
    stats = st.columns(3, gap="medium")
    stat_data = [
        ("⏰", "Reminders", reminder_count, "medications scheduled", "amber"),
        ("📊", "Recent records", recent_count, "activity and assessments", "blue"),
        ("❤️", "Health status", safe_text(status), "from latest triage", "green"),
    ]
    for col, (icon, label, value, sub, tone) in zip(stats, stat_data):
        with col:
            st.markdown(f"<div class='ms-stat-card {tone}'><div class='ms-stat-icon'>{icon}</div><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>", unsafe_allow_html=True)

    st.markdown("<div class='ms-section-title ms-home-section-gap'><h3>Recent Activity</h3></div>", unsafe_allow_html=True)
    activity_items = []
    for _, row in recent_triage.iterrows():
        activity_items.append(("🩺", "Symptom assessment", row.get("created_at", ""), row.get("predicted_urgency", "")))
    activity_items += [("🔸", x["title"], x["time"], x["detail"]) for x in st.session_state.activity_log[:5]]
    if activity_items:
        for icon, title, when, detail in activity_items[:5]:
            st.markdown(f"<div class='ms-activity-row'><div class='ms-activity-icon'>{icon}</div><div class='ms-activity-main'><strong>{safe_text(title)}</strong><span>{safe_text(detail)}</span></div><small>{safe_text(when)}</small></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>🗒️</div><strong>No recent activity</strong><p>Your assessments, scans and uploads will appear here.</p></div>", unsafe_allow_html=True)

    st.markdown("<div class='ms-health-tip'><div class='ms-tip-icon ms-pulse-icon'>✨</div><div><strong>Health Tips for You</strong><p>Drink enough water, maintain a balanced diet, get regular exercise, and prioritize good sleep for a healthier life.</p></div><span class='ms-tip-arrow'>→</span></div>", unsafe_allow_html=True)

# ============================================================
# PAGE NAVIGATION
# ============================================================

# The app now behaves like separate pages instead of rendering
# every feature beside the others.
active_feature = st.session_state.get("active_feature", "Home")

# ============================================================
# REMINDER ALERTS — sound + notification when a reminder is due
# ============================================================
# Three cooperating pieces:
#   1. Server side — every rerun, if a reminder's scheduled minute has
#      arrived, add an in-app notification + activity entry + st.toast.
#      Fires once per scheduled minute per session (reminder_alerts_fired).
#   2. Client side — a small component iframe (runs real JS, unlike
#      st.markdown which cannot execute <script> tags) ticks every 10s on
#      every logged-in page. At the scheduled minute it plays an alert
#      beep (Web Audio) and turns the strip red/flashing: "TIME TO TAKE
#      YOUR MEDICINE". Between reminders it shows the next reminder ETA.

if st.session_state.reminders:

    if "reminder_alerts_fired" not in st.session_state:
        st.session_state.reminder_alerts_fired = set()

    _today_key = datetime.datetime.now().astimezone().date().isoformat()
    _now_key = datetime.datetime.now().strftime("%H:%M")
    for _index, _rem in enumerate(st.session_state.reminders):
        _rem_id = str(_rem.get("id") or f"{_index}:{_rem.get('name', '')}")
        _rem_key = _rem["time"].strftime("%H:%M")
        _occurrence_key = f"{_today_key}|{_rem_id}|{_rem_key}"
        if (
            _rem_key == _now_key
            and _occurrence_key not in st.session_state.reminder_alerts_fired
        ):
            add_notification(
                f"⏰ Medication reminder due now: {_rem['name']}",
                "warning"
            )
            log_activity(
                f"Reminder due: {_rem['name']}",
                "Reminder",
                _rem["time"].strftime("%I:%M %p")
            )
            st.toast(f"⏰ {_rem['name']} — time to take your medicine", icon="⏰")
            st.session_state.reminder_alerts_fired.add(_occurrence_key)

    _reminders_js = json.dumps(
        [
            {
                "id": str(r.get("id") or index),
                "t": r["time"].strftime("%H:%M"),
                "name": str(r.get("name", "")),
            }
            for index, r in enumerate(st.session_state.reminders)
        ],
        ensure_ascii=True,
    ).replace("</", "<\\/")

    components.html(
        """
        <!doctype html>
        <html>
        <head>
        <meta charset="utf-8">
        <style>
        body{margin:0;padding:0;background:transparent;font-family:'Segoe UI',Arial,sans-serif}
        #strip{display:flex;align-items:center;min-height:44px;padding:8px 16px;
               border-radius:12px;border:1px solid #d9ece9;background:linear-gradient(90deg,#f0fbfa,#eaf7f5);
               color:#0e5a54;font-size:14px;font-weight:600;box-sizing:border-box}
        #strip.due{background:#b91c1c;color:#fff;border-color:#7f1d1d;animation:msflash 1s infinite}
        @keyframes msflash{0%,100%{opacity:1}50%{opacity:.55}}
        </style>
        </head>
        <body><div id="strip">⏰ Checking reminders…</div>
        <script>
        (function () {
            var reminders = __REMINDERS__;
            var strip = document.getElementById("strip");
            var dayKey = new Date().toDateString();
            var fired = {};
            function pad(n) { return ("0" + n).slice(-2); }
            function fmt(t) {
                var h = parseInt(t.slice(0, 2), 10), m = t.slice(3);
                var ap = h >= 12 ? "PM" : "AM";
                var hh = h % 12; if (hh === 0) hh = 12;
                return hh + ":" + m + " " + ap;
            }
            function beep() {
                try {
                    var C = window.AudioContext || window.webkitAudioContext;
                    if (!C) return;
                    var ctx = new C();
                    if (ctx.state === "suspended") ctx.resume();
                    var now = ctx.currentTime;
                    [0, 0.3, 0.6, 0.9, 1.2].forEach(function (off) {
                        var o = ctx.createOscillator(), g = ctx.createGain();
                        o.type = "sine"; o.frequency.value = 880;
                        g.gain.setValueAtTime(0.4, now + off);
                        g.gain.exponentialRampToValueAtTime(0.001, now + off + 0.25);
                        o.connect(g); g.connect(ctx.destination);
                        o.start(now + off); o.stop(now + off + 0.3);
                    });
                } catch (e) {}
            }
            function dueItems(hm) {
                return reminders.filter(function (r) { return r.t === hm; });
            }
            function render(hm) {
                var due = dueItems(hm);
                if (due.length) {
                    strip.className = "due";
                    strip.textContent = "🔔 TIME TO TAKE YOUR MEDICINE — " +
                        due.map(function (r) { return r.name; }).join(", ");
                    return;
                }
                var next = null;
                reminders.forEach(function (r) { if (r.t > hm && (!next || r.t < next.t)) next = r; });
                if (next) {
                    strip.className = "";
                    strip.textContent = "⏰ Next reminder: " + next.name + " at " + fmt(next.t);
                } else if (reminders.length) {
                    strip.className = "";
                    strip.textContent = "⏰ " + reminders.length + " reminder" +
                        (reminders.length > 1 ? "s" : "") + " set for today — you'll be alerted when one is due.";
                } else {
                    strip.style.display = "none";
                }
            }
            function check() {
                var d = new Date();
                var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
                var due = dueItems(hm);
                var occurrenceKey = dayKey + "|" + hm + "|" +
                    due.map(function (r) { return r.id; }).join(",");
                if (due.length && !fired[occurrenceKey]) {
                    fired[occurrenceKey] = true;
                    beep();
                }
                render(hm);
            }
            check();
            setInterval(check, 10000);
            setInterval(function () {
                var dk = new Date().toDateString();
                if (dk !== dayKey) { dayKey = dk; fired = {}; }
            }, 60000);
        })();
        </script>
        </body>
        </html>
        """.replace("__REMINDERS__", _reminders_js),
        height=56,
    )

# ============================================================
# PAGE BACK CONTROL
# ============================================================

if active_feature != "Home":
    back_left, back_right = st.columns([0.18, 0.82])

    with back_left:
        if st.button(
            "←  Back to Home",
            key="back_to_home",
            use_container_width=True
        ):
            st.session_state.active_feature = "Home"
            st.rerun()

    st.markdown(
        f"<div class='page-location'>Home  /  <strong>{active_feature}</strong></div>",
        unsafe_allow_html=True
    )


# ============================================================
# GLOBAL SEARCH
# ============================================================
if active_feature == "Search":
    st.markdown("<div class='ms-page-enter'><h2>Global Search</h2><p class='ms-page-subtitle'>Search medicines, records, hospitals and reminders from one place.</p></div>", unsafe_allow_html=True)
    query = st.text_input("Search", placeholder="Search medicines, reports, hospitals, triage results or reminders...", key="global_search_input")
    if query.strip():
        q = query.lower().strip()
        found = False
        try:
            med_df = pd.read_csv(MEDICINE_DATA_PATH)
            med_matches = med_df[med_df.astype(str).apply(lambda col: col.str.lower().str.contains(q, na=False)).any(axis=1)].head(5)
            if not med_matches.empty:
                found = True
                st.markdown("#### Medicines")
                for _, row in med_matches.iterrows():
                    st.markdown(f"<div class='ms-search-result'><strong>{safe_text(row.get('name',''))}</strong><span>{safe_text(row.get('used_for',''))} · {safe_text(row.get('form',''))}</span></div>", unsafe_allow_html=True)
        except Exception: pass
        triage = get_recent_triage(20)
        if not triage.empty:
            matches = triage[triage.astype(str).apply(lambda col: col.str.lower().str.contains(q, na=False)).any(axis=1)]
            if not matches.empty:
                found = True
                st.markdown("#### Previous triage")
                st.dataframe(matches, width="stretch", hide_index=True)
        reminder_matches = [r for r in st.session_state.reminders if q in r.get("name", "").lower() or q in r.get("notes", "").lower()]
        if reminder_matches:
            found = True
            st.markdown("#### Reminders")
            for r in reminder_matches:
                st.markdown(f"<div class='ms-search-result'><strong>{safe_text(r['name'])}</strong><span>{r['time'].strftime('%I:%M %p')} · {safe_text(r['food'])}</span></div>", unsafe_allow_html=True)
        try:
            price_df = pd.read_csv(PRICE_DATA_PATH)
            hospital_matches = price_df[price_df.astype(str).apply(lambda col: col.str.lower().str.contains(q, na=False)).any(axis=1)].head(5)
            if not hospital_matches.empty:
                found = True
                st.markdown("#### Hospitals / providers")
                st.dataframe(hospital_matches[[c for c in ['provider','city','district','procedure','price_inr','rating'] if c in hospital_matches.columns]], width="stretch", hide_index=True)
        except Exception: pass
        docs = [d for d in st.session_state.documents if q in d['name'].lower()]
        if docs:
            found = True
            st.markdown("#### Documents")
            for d in docs:
                st.markdown(f"<div class='ms-search-result'><strong>{safe_text(d['name'])}</strong><span>Uploaded {safe_text(d['uploaded_on'])}</span></div>", unsafe_allow_html=True)
        if not found:
            st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>🔍</div><strong>No results found</strong><p>Try a medicine name, hospital, symptom or report name.</p></div>", unsafe_allow_html=True)

# ============================================================
# PROFILE
# ============================================================
if active_feature == "Profile":
    st.markdown("<div class='ms-page-enter'><h2>Profile</h2><p class='ms-page-subtitle'>Manage your personal information and account preferences.</p></div>", unsafe_allow_html=True)
    st.markdown(f"<div class='ms-profile-card'><div class='ms-avatar'>{safe_text(st.session_state.user_name[:1].upper())}</div><div><h3>{safe_text(st.session_state.user_name)}</h3><p>{safe_text(st.session_state.username)}</p></div></div>", unsafe_allow_html=True)
    st.markdown("### Personal Information")
    p1, p2 = st.columns(2)
    with p1: st.text_input("Name", value=st.session_state.user_name, disabled=True)
    with p2: st.text_input("Username", value=st.session_state.username, disabled=True)
    st.markdown("### Emergency Contact")
    ec = st.session_state.emergency_contact
    st.info(f"{ec['name'] or 'Not set'} · {ec['phone'] or 'No phone'} · {ec['relation'] or 'Relation not set'}")
    st.markdown("### Account")
    a1, a2, a3 = st.columns(3)
    with a1:
        if st.button("Security settings", use_container_width=True): st.session_state.active_feature = "Account Settings"
    with a2:
        if st.button("Privacy center", use_container_width=True): st.session_state.active_feature = "Privacy & Security"
    with a3:
        if st.button("Logout", use_container_width=True):
            logout_user()
            st.rerun()

# ============================================================
# NOTIFICATIONS
# ============================================================
if active_feature == "Notifications":
    st.markdown("<div class='ms-page-enter'><h2>Notifications</h2><p class='ms-page-subtitle'>Important updates and reminders.</p></div>", unsafe_allow_html=True)
    if not st.session_state.notifications:
        st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>🔕</div><strong>You're all caught up</strong><p>New reminders and important activity will appear here.</p></div>", unsafe_allow_html=True)
    else:
        for n in st.session_state.notifications:
            st.markdown(f"<div class='ms-notification-row'><strong>{safe_text(n['message'])}</strong><small>{safe_text(n['time'])}</small></div>", unsafe_allow_html=True)
        if st.button("Clear notifications"):
            st.session_state.notifications = []
            st.rerun()

# ============================================================
# PRIVACY & SECURITY
# ============================================================
if active_feature == "Privacy & Security":
    st.markdown("<div class='ms-page-enter'><h2>Privacy & Security</h2><p class='ms-page-subtitle'>Understand and control the data used by this prototype.</p></div>", unsafe_allow_html=True)
    st.markdown("<div class='ms-privacy-grid'><div class='ms-privacy-card'><strong>Local records</strong><span>Your triage history, reminders, chat, contacts and saved medicines are stored in this app's local database, tied to your account.</span></div><div class='ms-privacy-card'><strong>Passwords</strong><span>Passwords are salted and hashed with PBKDF2-HMAC-SHA256 before they are stored, so the original password is never saved.</span></div><div class='ms-privacy-card'><strong>Documents</strong><span>Only each upload's name, type and size are stored — the file contents are never saved or read.</span></div></div>", unsafe_allow_html=True)
    st.markdown("### Manage Data")
    if st.button("Clear stored documents & chat"):
        clear_stored_documents()
        clear_stored_chat()
        st.session_state.documents = []
        st.session_state.chat_messages = []
        st.success("Stored documents and chat cleared.")
    st.markdown("### Delete Account")
    confirm = st.checkbox(
        "I understand that deleting my account removes my saved account records.",
        key="privacy_delete_confirm",
    )
    if st.button("Delete my account", type="secondary", disabled=not confirm):
        conn = get_connection(); cur = conn.cursor()
        for table in ["triage_history", "emergency_contacts", "medicine_scans", "reminders", "documents", "chat_history", "sessions"]:
            try: cur.execute(f"DELETE FROM {table} WHERE user_id = ?", (st.session_state.user_id,))
            except Exception: pass
        cur.execute("DELETE FROM users WHERE id = ?", (st.session_state.user_id,)); conn.commit(); conn.close()
        logout_user()
        st.rerun()
    st.caption("For a real deployment, review your privacy policy, retention rules, and healthcare-data requirements with a qualified professional.")

# ============================================================
# ACCESSIBILITY
# ============================================================
if active_feature == "Accessibility":
    st.markdown("<div class='ms-page-enter'><h2>Accessibility</h2><p class='ms-page-subtitle'>Adjust the interface for easier reading and navigation.</p></div>", unsafe_allow_html=True)
    # These two keys are the widgets' own keys, and Streamlit has already
    # written each toggle's value into st.session_state by the time it
    # returns. Writing the return value back to the same key would raise
    # StreamlitWidgetAlreadyInstantiatedError, so the widgets are called
    # for their side effect only. The flags are read near the top of the
    # run, before this page is reached, so the CSS is already applied for
    # whatever they now hold.
    st.toggle("Larger text", key="accessibility_large_text")
    st.toggle("High contrast", key="accessibility_high_contrast")
    # Save the preferences just toggled, so they outlive a page reload.
    save_persist_ui_state()
    st.markdown("Keyboard-friendly Streamlit controls and visible labels are used throughout the interface.")

# ============================================================
# OPTIONAL LOADING SPINNER COMPONENT
# ============================================================

if active_feature == "Loading Spinners":
    st.markdown(
        """
        <div class="ms-spinner-page">
            <h2>Loading Spinners</h2>
            <p>Reusable loading states for MediScan AI.</p>

            <div class="ms-spinner-grid">
                <div class="ms-spinner-card">
                    <h4>Basic Spinner</h4>
                    <div class="ms-spinner"></div>
                </div>

                <div class="ms-spinner-card">
                    <h4>Loading Button</h4>
                    <div class="ms-processing-button">
                        <span class="ms-button-spinner"></span>
                        <span>Processing...</span>
                    </div>
                </div>

                <div class="ms-spinner-card">
                    <h4>Dots Loader</h4>
                    <div class="ms-dots">
                        <span></span><span></span><span></span>
                    </div>
                </div>

                <div class="ms-spinner-card">
                    <h4>Pulse Loader</h4>
                    <div class="ms-pulse"></div>
                </div>
            </div>

            <div class="ms-spinner-overlay-demo">
                <div class="ms-loading-content">
                    <div class="ms-spinner"></div>
                    <p>Loading content...</p>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

# ============================================================
# Emergency keyword list (unchanged safety logic)
# ============================================================

emergency_keywords = [
    "severe chest pain",
    "chest pain",
    "difficulty breathing",
    "trouble breathing",
    "shortness of breath",
    "unconscious",
    "loss of consciousness",
    "heavy bleeding",
    "uncontrolled bleeding",
    "seizure",
    "sudden weakness",
    "one-sided weakness",
    "severe allergic reaction",
    "anaphylaxis"
]

# ============================================================
# TAB 1 — Symptom Triage
# ============================================================

if active_feature == "Triage":

    st.header(T["tab_triage"])

    # ------------------------------------------------------------
    # The result is stored in session_state and rendered SEPARATELY
    # from the Analyze button. Streamlit buttons only return True for
    # the single run triggered by the click — if the output lived
    # inside `if st.button(...)` it would vanish on the very next
    # rerun (any widget change in the sidebar or the form itself),
    # leaving the user with a fresh empty form. Persisting the result
    # keeps the analysis on screen, scrollable, until a new one starts.
    # ------------------------------------------------------------
    result = st.session_state.get("triage_result")

    if result is not None:
        # ---------------- SHOW THE ANALYSIS RESULT ----------------
        prediction = result["prediction"]
        is_emergency = result.get("is_emergency", False)
        confidence = result.get("confidence")
        probabilities = result.get("probabilities")
        classes = result.get("classes")

        st.subheader("🤖 AI Triage Result")

        if is_emergency or prediction == "Emergency":
            st.error("🚨 Predicted Urgency: EMERGENCY")
        elif prediction == "Low":
            st.success("🟢 Predicted Urgency: LOW")
        elif prediction == "Moderate":
            st.warning("🟡 Predicted Urgency: MODERATE")
        elif prediction == "High":
            st.error("🔴 Predicted Urgency: HIGH")
        else:
            st.info(f"Predicted Urgency: {prediction}")

        st.markdown(f"**Symptoms entered:**\n\n{result['symptoms']}")

        if is_emergency:
            st.error(
                "Emergency symptoms were detected. "
                "Please seek immediate medical attention "
                "from a qualified healthcare professional."
            )
            st.metric("Safety Override", "Emergency")

        if confidence is not None:
            st.write(f"**AI Confidence:** {confidence:.2f}%")
            st.progress(min(int(confidence), 100))

        if probabilities is not None and classes:
            st.subheader("📊 Urgency Probability")
            for class_name, probability in zip(classes, probabilities):
                percentage = probability * 100
                st.write(f"**{class_name}: {percentage:.2f}%**")
                st.progress(min(int(percentage), 100))

        diet_tip = result.get("diet_tip")
        if diet_tip:
            st.subheader(T["diet_suggestion_title"])
            st.info(diet_tip)
            st.caption(DIET_DISCLAIMER)

        st.caption(
            "⚠️ This is an educational AI prototype and is "
            "not a medical diagnosis."
        )

        if st.button("➕ New Analysis", use_container_width=True):
            st.session_state.triage_result = None
            st.rerun()

    else:
        # ---------------- INPUT FORM ----------------
        symptoms = st.text_area(
            T["describe_symptoms"],
            placeholder="Example: I have fever and cough for 3 days...",
            height=120,
            key="triage_symptoms",
        )

        age = st.number_input(
            T["age"], min_value=1, max_value=120, value=22, key="triage_age"
        )

        duration = st.text_input(
            T["duration_q"],
            placeholder="Example: 3 days",
            key="triage_duration",
        )

        severity = st.selectbox(
            T["severity_q"], ["Mild", "Moderate", "Severe"], key="triage_severity"
        )

        st.caption(
            "Analysis runs instantly once warmed up; on a freshly-started cloud "
            "server the first run can take a minute or two — please wait."
        )

        if st.button(T["analyze_btn"], use_container_width=True):

            if symptoms.strip():

                with st.spinner("🤖 Analyzing your symptoms..."):

                    duration_match = re.search(r"\d+", duration)
                    duration_days = int(duration_match.group()) if duration_match else 1

                    severity_scores = {"Mild": 1, "Moderate": 2, "Severe": 3}
                    severity_score = severity_scores[severity]

                    input_data = pd.DataFrame({
                        "symptoms": [symptoms],
                        "age": [age],
                        "severity": [severity],
                        "duration_days": [duration_days],
                        "severity_score": [severity_score]
                    })

                    symptoms_lower = symptoms.lower()

                    is_emergency = any(
                        keyword in symptoms_lower
                        for keyword in emergency_keywords
                    )

                    try:
                        if is_emergency:
                            prediction = "Emergency"
                            confidence = 100.0
                            probabilities = None
                            classes = None
                        elif model is None:
                            # The saved model failed to load. The emergency
                            # keyword check above is independent of it, so
                            # report that path still works rather than
                            # crashing on a None predict().
                            st.warning(
                                "The triage model could not be loaded, so only "
                                "the emergency keyword check was applied. Please "
                                "consult a doctor rather than relying on this "
                                "result."
                            )
                            raise RuntimeError("triage model unavailable")
                        else:
                            prediction = model.predict(input_data)[0]

                            if hasattr(model, "predict_proba"):
                                probabilities = model.predict_proba(input_data)[0]
                                confidence = max(probabilities) * 100
                                classes = list(model.classes_)
                            else:
                                probabilities = None
                                confidence = None
                                classes = None

                        # General diet suggestion (only for a few keywords)
                        matched_tip = None
                        for keyword, tip in DIET_SUGGESTIONS.items():
                            if keyword in symptoms_lower:
                                matched_tip = tip
                                break

                        # ---- Save this check to the local database ----
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            INSERT INTO triage_history
                            (user_id, symptoms, age, severity, duration, predicted_urgency)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (st.session_state.user_id, symptoms, age, severity, duration, prediction)
                        )
                        conn.commit()
                        conn.close()
                        log_activity("Symptom assessment", "Triage", str(prediction))
                        add_notification("New triage report available", "success")

                        # Persist the result so it survives future reruns and
                        # can be scrolled/read at leisure.
                        st.session_state.triage_result = {
                            "symptoms": symptoms,
                            "prediction": prediction,
                            "confidence": confidence,
                            "probabilities": probabilities,
                            "classes": classes,
                            "is_emergency": is_emergency,
                            "diet_tip": matched_tip,
                        }
                        st.rerun()

                    except Exception as e:
                        st.error("An error occurred while analyzing the symptoms.")
                        st.code(str(e))

            else:
                st.warning("Please enter your symptoms before analyzing.")

# ============================================================
# TAB 2 — Hospital Finder (district + rating + maps + suggestions)
# ============================================================

if active_feature == "Hospital Finder":

    st.header(T["tab_hospitals"])

    try:
        price_data = pd.read_csv(PRICE_DATA_PATH, sep="\t")
        price_data.columns = price_data.columns.str.strip()

        # -------- City --------
        selected_city = st.selectbox(
            T["select_city"],
            sorted(price_data["city"].dropna().unique()),
            index=0,
            placeholder="Choose a city...",
            key="hospital_city",
        )

        city_data = price_data[price_data["city"] == selected_city]

        # -------- Revenue District (only shown when a city has more than one) --------
        available_regions = sorted(city_data["region"].dropna().unique())

        if len(available_regions) > 1:
            selected_region = st.selectbox(
                T["select_region"],
                available_regions,
                index=0,
                placeholder="Choose a revenue district...",
                key="hospital_region",
            )
            region_data = city_data[city_data["region"] == selected_region]
        else:
            region_data = city_data

        # -------- District / Mandal --------
        selected_district = st.selectbox(
            T["select_district"],
            sorted(region_data["district"].dropna().unique()),
            index=0,
            placeholder="Choose a district / mandal...",
            key="hospital_district",
        )

        # -------- Procedure (search + select) --------
        all_procedures = sorted(region_data["procedure"].dropna().unique())

        procedure_search = st.text_input(
            "🔍 Search Medical Test / Procedure",
            placeholder="Type to search — e.g. 'MRI', 'Blood', 'Scan'...",
            key="procedure_search",
        )

        if procedure_search.strip():
            matched_procedures = [
                p for p in all_procedures
                if procedure_search.strip().lower() in p.lower()
            ]
            if not matched_procedures:
                st.warning(
                    f"No procedure matches '{procedure_search}'. Showing all procedures instead."
                )
                matched_procedures = all_procedures
        else:
            matched_procedures = all_procedures

        selected_procedure = st.selectbox(
            T["select_procedure"],
            matched_procedures,
            index=0,
            placeholder="Choose a medical test or procedure...",
            key="selected_procedure",
        )

        # Scoped to the selected revenue district (not the whole city) so
        # "other districts" stays a manageable, nearby list instead of
        # every one of the 70+ mandals across the whole metro area.
        procedure_scope_data = region_data[region_data["procedure"] == selected_procedure].copy()

        in_district = procedure_scope_data[
            procedure_scope_data["district"] == selected_district
        ].copy()

        out_of_district = procedure_scope_data[
            procedure_scope_data["district"] != selected_district
        ].copy()

        # --------------------------------------------------------
        # Best Hospital (highest rating, price as tie-breaker)
        # --------------------------------------------------------
        all_candidates = procedure_scope_data.sort_values(
            by=["rating", "price_inr"], ascending=[False, True]
        )

        if not all_candidates.empty:
            best = all_candidates.iloc[0]
            best_map_link = get_maps_link(best["provider"], best["district"], selected_city)

            st.subheader(T["best_hospital"])
            st.markdown(
                f"""
                <div class="best-hospital-card">
                <b>{best['provider']}</b> — {best['district']}<br>
                ⭐ Rating: {best['rating']} / 5.0 &nbsp;|&nbsp;
                💰 Price: ₹{best['price_inr']:,.0f} &nbsp;|&nbsp;
                <a href="tel:{best['phone']}">{best['phone']}</a> &nbsp;|&nbsp;
                <a href="{best_map_link}" target="_blank">{T['location_link_text']}</a>
                </div>
                """,
                unsafe_allow_html=True
            )

            if best["district"] != selected_district:
                st.caption(
                    f"ℹ️ The top-rated option is in **{best['district']}**, "
                    f"not your selected district. See 'other districts' below."
                )

        # --------------------------------------------------------
        # Price Summary (based on in-district hospitals)
        # --------------------------------------------------------
        if not in_district.empty:
            st.subheader("💰 Price Summary — Your District")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Lowest Price", f"₹{in_district['price_inr'].min():,.0f}")
            with col2:
                st.metric("Average Price", f"₹{in_district['price_inr'].mean():,.0f}")
            with col3:
                st.metric("Highest Price", f"₹{in_district['price_inr'].max():,.0f}")

        # --------------------------------------------------------
        # In-district hospitals table (with maps link column)
        # --------------------------------------------------------
        st.subheader(f"🏥 {T['in_district']}: {selected_district}")

        if in_district.empty:
            st.warning("No hospitals found in this district for this procedure.")
        else:
            in_view = in_district[["provider", "price_inr", "rating", "phone", "district"]].sort_values(
                by=["rating", "price_inr"], ascending=[False, True]
            ).copy()
            in_view["Map"] = in_view.apply(
                lambda r: get_maps_link(r["provider"], r["district"], selected_city), axis=1
            )
            in_view = in_view.drop(columns=["district"]).rename(columns={
                "provider": "Hospital", "price_inr": "Price (₹)",
                "rating": "Rating", "phone": "Phone"
            })
            st.dataframe(
                in_view,
                width="stretch",
                hide_index=True,
                column_config={
                    "Map": st.column_config.LinkColumn("Map", display_text=T["location_link_text"])
                }
            )

        # --------------------------------------------------------
        # Out-of-district (out of surroundings) suggestions
        # --------------------------------------------------------
        st.subheader(f"🌆 {T['other_district']}")

        if out_of_district.empty:
            st.write("No other districts found in this revenue district.")
        else:
            out_view = out_of_district[
                ["provider", "district", "price_inr", "rating", "phone"]
            ].sort_values(by=["rating", "price_inr"], ascending=[False, True]).copy()
            out_view["Map"] = out_view.apply(
                lambda r: get_maps_link(r["provider"], r["district"], selected_city), axis=1
            )
            out_view = out_view.rename(columns={
                "provider": "Hospital", "district": "District",
                "price_inr": "Price (₹)", "rating": "Rating", "phone": "Phone"
            })
            st.dataframe(
                out_view,
                width="stretch",
                hide_index=True,
                column_config={
                    "Map": st.column_config.LinkColumn("Map", display_text=T["location_link_text"])
                }
            )

        # --------------------------------------------------------
        # Charts
        # --------------------------------------------------------
        st.subheader("📊 Price Comparison — Your District")
        if not in_district.empty:
            chart_price = in_district.set_index("provider")["price_inr"]
            st.bar_chart(chart_price)

        st.subheader("📈 Rating vs Price — All Hospitals (this district, this test)")
        st.caption("Top-left of this chart (high rating, low price) is generally the best value.")
        scatter_view = procedure_scope_data[["price_inr", "rating", "provider"]].rename(
            columns={"price_inr": "Price (₹)", "rating": "Rating"}
        )
        st.scatter_chart(scatter_view, x="Price (₹)", y="Rating")

        # --------------------------------------------------------
        # Savings
        # --------------------------------------------------------
        if not in_district.empty:
            lowest_price = in_district["price_inr"].min()
            highest_price = in_district["price_inr"].max()
            savings_amount = highest_price - lowest_price
            savings_percentage = (savings_amount / highest_price * 100) if highest_price > 0 else 0

            st.subheader("💡 Potential Savings")
            st.success(
                f"Choosing the lowest-priced provider in your district could save "
                f"₹{savings_amount:,.0f} ({savings_percentage:.2f}%) "
                f"compared with the highest-priced provider there."
            )

        st.caption(
            "⚠️ Prices, districts, ratings and phone numbers shown are sample/"
            "estimated data for this educational prototype and do not represent "
            "actual providers. Map links open a Google Maps search for the "
            "hospital's name and area — they are not pinned to real GPS "
            "coordinates."
        )

    except FileNotFoundError:
        st.error(
            "Healthcare price dataset not found. "
            "Please make sure healthcare_prices.csv is inside the data folder."
        )
    except Exception as e:
        st.error(f"Unable to load healthcare price dataset: {e}")

# ============================================================
# TAB 3 — Medicine Scanner (camera OCR + manual search)
# ============================================================

if active_feature == "Medicine Scanner":

    st.header(T["tab_scanner"])

    try:
        medicines_df = pd.read_csv(MEDICINE_DATA_PATH)
    except FileNotFoundError:
        medicines_df = None
        st.error(
            "Medicine database not found. Please make sure medicines.csv "
            "is inside the data folder."
        )

    if medicines_df is not None:

        st.caption(
            "Photo scanning checks a small demo database "
            f"({len(medicines_df)} entries) and OCR can misread a blurry or "
            "angled photo. Typing a name searches that list first and then the "
            "web, so almost any medicine can be found. Always double-check with "
            "a pharmacist before taking anything."
        )

        matched_medicine = None
        col_cam, col_manual = st.columns(2)

        with col_cam:
            st.markdown("**📷 Scan with Camera / Upload Photo**")

            if not OCR_AVAILABLE:
                st.warning(
                    "OCR libraries (pytesseract, Pillow) aren't installed. "
                    "Run `pip install pytesseract Pillow` and make sure the "
                    "Tesseract OCR program itself is installed on your system "
                    "(it's separate from the pip package)."
                )
            else:
                photo = st.camera_input(
                    "Take a photo of the medicine strip/box", key="medicine_camera"
                )
                uploaded_photo = st.file_uploader(
                    "...or upload a photo instead",
                    type=["png", "jpg", "jpeg"],
                    key="medicine_photo_uploader",
                )
                image_source = photo or uploaded_photo

                if image_source is not None:
                    image = Image.open(image_source)
                    try:
                        ocr_loading = st.empty()
                        ocr_loading.markdown(
                            """
                            <div class="ms-inline-loader ms-inline-loader-small">
                                <div class="ms-loading-content">
                                    <div class="ms-spinner"></div>
                                    <p>Scanning medicine image...</p>
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

                        extracted_text = pytesseract.image_to_string(image)
                        ocr_loading.empty()

                        with st.expander("🔎 Raw text detected from image"):
                            st.text(extracted_text if extracted_text.strip() else "(nothing detected)")

                        matched_medicine = find_medicine(extracted_text, medicines_df)
                        
                        # If web search is enabled, also search public databases
                        # using the extracted text as the query
                        web_lookup_on = st.session_state.get("medicine_web_toggle", True)
                        if web_lookup_on and extracted_text.strip():
                            with st.spinner("Searching public medicine databases for scanned text..."):
                                st.session_state.medicine_lookup = {
                                    "query": extracted_text.strip()[:200],
                                    "web": True,
                                    "language": assistant_code(st.session_state.assistant_language),
                                    "result": search_medicine(
                                        extracted_text.strip()[:200],
                                        assistant_code(st.session_state.assistant_language)
                                    ),
                                }
                                st.session_state.medicine_lookup["language"] = assistant_code(st.session_state.assistant_language)
                            st.session_state.medicine_ai_summary = None

                        if matched_medicine is None:
                            st.warning(
                                "Couldn't confidently match this photo to a medicine "
                                "in the demo database. Try the manual search instead."
                            )

                    except Exception as e:
                        st.error(
                            "OCR failed — Tesseract OCR may not be installed on this "
                            "system, or isn't on PATH."
                        )
                        st.caption(
                            "Windows: install from "
                            "https://github.com/UB-Mannheim/tesseract/wiki, then set "
                            "the TESSERACT_PATH environment variable to its .exe path."
                        )
                        st.code(str(e))

        with col_manual:
            st.markdown("**⌨️ Or Type the Medicine Name**")
            st.caption(
                "Searches public medicine databases (openFDA, RxNorm, Wikipedia) "
                "for almost any medicine name — Indian brands, prescription drugs, etc. "
                "The demo list is checked as a fast offline supplement."
            )
            manual_query = st.text_input(
                "Medicine name",
                placeholder="e.g. Paracetamol, Dolo 650, Augmentin, Meftal-P...",
                key="medicine_manual_query",
            )
            search_cols = st.columns([2, 1])
            with search_cols[0]:
                run_medicine_search = st.button(
                    T["scan_medicine_btn"],
                    type="primary",
                    use_container_width=True,
                )
            with search_cols[1]:
                web_lookup_on = st.toggle(
                    "Search the web",
                    value=True,
                    key="medicine_web_toggle",
                    help=(
                        "Also look the name up in openFDA, RxNorm, and "
                        "Wikipedia. Turn this off to use only the demo list."
                    ),
                )

            if run_medicine_search and manual_query.strip():
                typed_name = manual_query.strip()

                # Always check the local demo database first (fast, offline)
                matched_medicine = find_medicine(manual_query, medicines_df)
                
                # The lookup result is cached in the session so it survives
                # reruns (and language switches) without hitting the network
                # again.
                st.session_state.medicine_lookup = {
                    "query": typed_name,
                    "web": web_lookup_on,
                    "language": assistant_code(st.session_state.assistant_language),
                    "result": None,
                }
                st.session_state.medicine_ai_summary = None
                
                # If web search is enabled, search public databases immediately
                # This allows finding almost any medicine name (Indian brands, etc.)
                if web_lookup_on:
                    with st.spinner(f"Searching public medicine databases for {typed_name}..."):
                        st.session_state.medicine_lookup["result"] = search_medicine(
                            typed_name, 
                            assistant_code(st.session_state.assistant_language)
                        )
                        st.session_state.medicine_lookup["language"] = assistant_code(st.session_state.assistant_language)
                    
                    web_result = st.session_state.medicine_lookup["result"]
                    if not web_result.get("matched") and matched_medicine is None:
                        st.warning(
                            "No match found in public databases or the demo list. "
                            "Try checking the spelling or use the web links below."
                        )
                    elif matched_medicine is None:
                        st.info(
                            "Found in public medicine databases. See web results below."
                        )
                elif matched_medicine is None:
                    st.warning("No close match found in the demo database. Enable 'Search the web' to search public databases.")

        if matched_medicine is not None:
            med_name = str(matched_medicine['name'])
            st.markdown(f"<div class='ms-medicine-result'><div class='ms-medicine-title'><span class='ms-medicine-icon'>M</span><div><span>Medicine identified</span><h2>{safe_text(med_name)}</h2></div></div><div class='ms-medicine-grid'><div><b>Purpose</b><span>{safe_text(matched_medicine['used_for'])}</span></div><div><b>Composition</b><span>{safe_text(matched_medicine['composition'])}</span></div><div><b>Form</b><span>{safe_text(matched_medicine['form'])}</span></div><div><b>Drug class</b><span>{safe_text(matched_medicine['drug_class'])}</span></div><div><b>Typical timing</b><span>{safe_text(matched_medicine['timing'])}</span></div><div><b>Approx. price</b><span>₹{float(matched_medicine['price_inr']):,.0f}</span></div></div></div>", unsafe_allow_html=True)
            st.warning("Follow your prescription or pharmacist's instructions. This scanner does not determine a personal dosage.")
            mc1, mc2 = st.columns(2)
            with mc1:
                if st.button("Set Reminder", key="med_set_reminder", use_container_width=True):
                    st.session_state.active_feature = "Reminders"
                    st.session_state.prefill_reminder = med_name
                    st.rerun()
            with mc2:
                if st.button("Save to Medicines", key="med_save", use_container_width=True):
                    if med_name not in st.session_state.saved_medicines:
                        conn = get_connection()
                        try:
                            # The unique index makes a repeat save a no-op
                            # instead of a duplicate row.
                            conn.execute(
                                "INSERT OR IGNORE INTO saved_medicines (user_id, medicine_name) VALUES (?, ?)",
                                (st.session_state.user_id, med_name),
                            )
                            conn.commit()
                        finally:
                            conn.close()
                        st.session_state.saved_medicines.append(med_name)
                        log_activity(f"{med_name} saved", "Medicine", "Saved medicine")
                        add_notification("Medicine saved successfully", "success")
                        st.success("Medicine saved to your list.")

        # -------------------- Web search result --------------------
        # Rendered outside the two columns so it gets the full page width, and
        # re-rendered from session state on every rerun.
        lookup = st.session_state.get("medicine_lookup")
        if lookup and lookup.get("query"):
            lookup_query = lookup["query"]
            lookup_language = assistant_code(st.session_state.assistant_language)
            st.divider()
            st.subheader(f"🌐 Web results for “{lookup_query}”")

            if lookup.get("web"):
                # Changing the reply language re-runs the lookup, because
                # Wikipedia is searched in that language first.
                if (
                    lookup.get("result") is None
                    or lookup.get("language") != lookup_language
                ):
                    with st.spinner(f"Searching public medicine databases for {lookup_query}..."):
                        lookup["result"] = search_medicine(
                            lookup_query, lookup_language
                        )
                    lookup["language"] = lookup_language
                web_result = lookup["result"]
                render_medicine_web_result(web_result, lookup_query)
                render_medicine_ai_summary(web_result, lookup_query)
            else:
                st.caption(
                    "Web search was off for this lookup, so only the demo "
                    "database above was used."
                )

# ============================================================
# TAB 4 — AI Assistant & Medication Reminders
# ============================================================

if active_feature == "AI Assistant":

    # ---------------- Reply language switch ----------------
    # The app language only sets the starting point; from here the user can
    # switch between English, Hindi, and Telugu on this screen alone.
    language_options = list(ASSISTANT_LANGUAGES.keys())
    if st.session_state.assistant_language not in language_options:
        st.session_state.assistant_language = "English"
    # Label the switch in the language currently in use, so a user who has
    # already switched to Telugu does not have to read English to switch back.
    reply_language = st.selectbox(
        assistant_ui(st.session_state.assistant_language)["reply_language"],
        language_options,
        index=language_options.index(st.session_state.assistant_language),
        key="assistant_language_switch",
        format_func=lambda name: ASSISTANT_LANGUAGES[name]["native"],
    )
    if reply_language != st.session_state.assistant_language:
        # A language change invalidates any cached medicine summary, because
        # that text is written in the previous language.
        st.session_state.assistant_language = reply_language
        st.session_state.assistant_language_manual = True
        st.session_state.medicine_ai_summary = None

    A = assistant_ui(reply_language)
    prompts = A["prompts"]

    st.markdown(
        f"""
        <div class="ai-page-header">
            <div class="ai-icon">AI</div>
            <div>
                <h2>{safe_text(A["title"])}</h2>
                <p>{safe_text(A["subtitle"])}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown(
        f"""
        <div class="ai-disclaimer">
            {safe_text(A["disclaimer"])}
        </div>
        """,
        unsafe_allow_html=True
    )

    if not GROQ_LIB_AVAILABLE:
        st.warning(A["no_lib"])
    elif GROQ_API_KEY is None:
        st.warning(A["no_key"])
        # A bare "no key found" cannot be acted on. Say which places were
        # checked and why the lookup came back empty, so a mistyped secret or a
        # wrong section heading is visible instead of looking like a bug.
        st.info(GROQ_KEY_PROBLEM or "No Groq API key is configured.")
        # The single most common cause of "it works locally but the deployed
        # assistant never answers": .streamlit/ is gitignored, so the local
        # secrets.toml is never deployed and the hosted app has no key at all.
        # Without the chat box being rendered there is nothing on this page to
        # reply to, which reads as a broken assistant rather than a missing
        # configuration value.
        st.error(describe_groq_key_missing())
    else:

        if not st.session_state.chat_messages:

            chips_html = "".join(
                f"<span>{safe_text(chip)}</span>" for chip in A["chips"]
            )
            st.markdown(
                f"""
                <div class="ai-welcome-card">
                    <div class="ai-welcome-icon">AI</div>
                    <h3>{safe_text(A["welcome"])}</h3>
                    <p>{safe_text(A["welcome_body"])}</p>
                    <div class="ai-suggestions">
                        {chips_html}
                    </div>
                    <div class="ai-ready-animation">
                        <span>{safe_text(A["ready"])}</span>
                        <span class="ms-status-dots"><i></i><i></i><i></i></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

            # One-tap questions, written in the selected language so a user
            # who cannot read English still has somewhere to start.
            for position, suggestion in enumerate(A["suggestions"]):
                if st.button(
                    suggestion,
                    key=f"ai_suggestion_{reply_language}_{position}",
                    use_container_width=True,
                ):
                    st.session_state.ai_suggested_question = suggestion
                    st.rerun()

        control_cols = st.columns(6)
        with control_cols[0]:
            if st.button(A["clear_chat"], use_container_width=True):
                clear_stored_chat()
                st.session_state.chat_messages = []
                st.rerun()
        with control_cols[1]:
            if st.button(A["explain_simple"], use_container_width=True):
                st.session_state.ai_prefill = prompts["explain_simple"]
                st.rerun()
        with control_cols[2]:
            if st.button(A["summarize"], use_container_width=True):
                st.session_state.ai_prefill = prompts["summarize"]
                st.rerun()
        with control_cols[3]:
            if st.button(A["regenerate"], use_container_width=True) and st.session_state.chat_messages:
                if st.session_state.chat_messages[-1]["role"] == "assistant":
                    # Drop the stale answer from both memory and the database,
                    # so the reply that replaces it is not appended twice.
                    delete_last_chat_message()
                    st.session_state.chat_messages.pop()
                st.session_state.ai_regenerate = True
                st.rerun()
        with control_cols[4]:
            # Switching language mid-chat only affects new answers; this turns
            # the last answer into the newly chosen language.
            last_answer = ""
            for message in reversed(st.session_state.chat_messages):
                if message["role"] == "assistant":
                    last_answer = message["content"]
                    break
            if st.button(
                A["translate"],
                use_container_width=True,
                disabled=not last_answer,
                key="ai_translate_btn",
            ):
                translate_loading = st.empty()
                translate_loading.markdown(
                    f"""
                    <div class="ms-inline-loader ms-ai-loader">
                        <div class="ms-loading-content">
                            <div class="ms-spinner"></div>
                            <p>{safe_text(A["thinking"])}</p>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                translated = ""
                try:
                    translated = ask_groq(
                        messages=[
                            {
                                "role": "system",
                                "content": build_translation_prompt(
                                    last_answer, reply_language
                                ),
                            }
                        ],
                        max_tokens=400,
                        temperature=0.2,
                    )
                except Exception as exc:
                    translated = ""
                    translate_loading.empty()
                    st.error(f"Translation failed: {exc}")
                translate_loading.empty()
                if translated:
                    if st.session_state.chat_messages[-1]["role"] == "assistant":
                        # Rewriting the last answer, so update its stored row.
                        update_last_chat_message(translated)
                        st.session_state.chat_messages[-1]["content"] = translated
                    else:
                        st.session_state.chat_messages.append(
                            {"role": "assistant", "content": translated}
                        )
                        save_chat_message("assistant", translated)
                    st.rerun()
        with control_cols[5]:
            st.caption(A["voice_input"])
            st.audio_input(A["record"], key="ai_voice_input")

        # A failed request is stashed in session_state by ask_groq, because the
        # chat path ends in st.rerun() and would otherwise wipe any warning
        # before the user could read it.
        if st.session_state.ai_last_error:
            # describe_groq_error() already explains the common causes (bad
            # key, rate limit, retired model) and keeps the raw SDK message, so
            # the full reason is shown rather than a generic apology.
            st.warning(st.session_state.ai_last_error)
        if st.session_state.chat_save_error:
            st.warning(st.session_state.chat_save_error)

        st.caption(
            f"Build {APP_BUILD} · model {GROQ_MODEL} · "
            f"API key {('from ' + GROQ_KEY_SOURCE) if GROQ_API_KEY else 'MISSING'} · "
            f"stage {st.session_state.ai_last_stage} · "
            f"last reply {st.session_state.ai_last_reply_len} chars"
        )

        # Live reachability check. `ask_groq` only runs when a question is
        # typed, so a broken deployment otherwise shows a perfectly normal
        # page and simply never answers. One authenticated GET /models costs
        # almost nothing and proves the key and model are usable before the
        # user wonders why. Run once per session, then re-run on demand and
        # whenever a request actually failed, so a fixed secret is picked up.
        if st.button("Check Groq connection", key="ai_check_groq"):
            st.session_state.groq_check_forced = True
        wants_check = (
            st.session_state.groq_check_forced
            or bool(st.session_state.ai_last_error)
            or st.session_state.get("groq_check") is None
        )
        if wants_check:
            groq_ok, groq_message = groq_status()
            st.session_state.groq_check = (groq_ok, groq_message)
            st.session_state.groq_check_failed = not groq_ok
            st.session_state.groq_check_forced = False

        groq_ok, groq_message = st.session_state.get("groq_check") or (False, "")
        if groq_ok:
            st.caption(groq_message)
        elif groq_message and st.session_state.get("groq_check_failed"):
            # Only shouted about when it has actually failed, so a working
            # assistant page stays quiet.
            st.error(groq_message)

        chat_box = st.container()

        with chat_box:

            for msg in st.session_state.chat_messages:

                if msg["role"] == "user":

                    st.markdown(
                        f"""
                        <div class="ai-message ai-user-message">
                            <div class="ai-message-label">{safe_text(A["you"])}</div>
                            <div>{safe_text(msg["content"])}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                else:

                    st.markdown(
                        f"""
                        <div class="ai-message ai-assistant-message">
                            <div class="ai-message-label">{safe_text(A["assistant"])}</div>
                            <div>{safe_text(msg["content"])}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

        # "Explain simply" and "Summarize" set ai_prefill and end the run. That
        # prompt is the question to send, so it is picked up here exactly like
        # a typed message. It used to be popped into st.info() and dropped,
        # which left both buttons looking like they did nothing.
        prefill = st.session_state.pop("ai_prefill", "")
        if prefill:
            st.info(prefill)
        user_question = st.chat_input(A["chat_placeholder"], key="ai_chat_input")
        if prefill:
            user_question = prefill
        # pop() twice removed the value on the first call, so the second one
        # always returned "" and every one-tap suggestion button was a no-op.
        suggested = st.session_state.pop("ai_suggested_question", "")
        if suggested:
            user_question = suggested
        if st.session_state.pop("ai_regenerate", False):
            user_question = prompts["regenerate"]

        if user_question:

            st.session_state.chat_messages.append(
                {
                    "role": "user",
                    "content": user_question
                }
            )
            save_chat_message("user", user_question)
            st.session_state.ai_last_stage = "user_saved"

            try:

                ai_loading = st.empty()
                ai_loading.markdown(
                    f"""
                    <div class="ms-inline-loader ms-ai-loader">
                        <div class="ms-loading-content">
                            <div class="ms-spinner"></div>
                            <p>{safe_text(A["thinking"])}</p>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                # The system prompt is rebuilt for the selected language, so
                # this is the switch case that decides the reply language.
                reply = ask_groq(
                    messages=(
                        [
                            {
                                "role": "system",
                                "content": build_assistant_prompt(reply_language)
                            }
                        ]
                        + st.session_state.chat_messages
                    ),
                    max_tokens=600,
                    temperature=0.4,
                )

                ai_loading.empty()

            except Exception as e:

                reply = f"Assistant error: {e}"
                st.session_state.ai_last_stage = f"handler_exception({e})"

            st.session_state.chat_messages.append(
                {
                    "role": "assistant",
                    "content": reply
                }
            )
            save_chat_message("assistant", reply)
            st.session_state.ai_last_stage = "assistant_saved"

            st.rerun()

elif active_feature == "Reminders":

    st.markdown(
        """
        <div class="ai-page-header">
            <div class="ai-icon">R</div>
            <div>
                <h2>Medication Reminders</h2>
                <p>Keep track of your medication schedule.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.caption(
        "While this app is open, a reminder rings at its scheduled time with "
        "an alert sound, a flashing on-screen notice and a toast notification. "
        "It is an in-app alert, not a background push notification."
    )

    with st.form("add_reminder_form"):

        r_name = st.text_input(
            "Medicine name",
            value=st.session_state.pop("prefill_reminder", ""),
            key="reminder_name",
        )

        r_form = st.selectbox(
            "Form",
            [
                "Tablet",
                "Capsule",
                "Syrup",
                "Liquid",
                "Injection",
                "Other"
            ],
            key="reminder_form",
        )

        r_food = st.selectbox(
            "Timing",
            [
                "Before Food",
                "After Food",
                "Either / Not applicable"
            ],
            key="reminder_food",
        )

        r_time = st.time_input(
            "Time to take it",
            value=datetime.time(9, 0),
            key="reminder_time",
        )

        r_notes = st.text_input(
            "Notes (optional)",
            placeholder="e.g. twice daily, with water",
            key="reminder_notes",
        )

        add_clicked = st.form_submit_button(
            T["add_reminder_btn"]
        )

        if add_clicked and r_name.strip():

            reminder_id = save_reminder(
                r_name, r_form, r_food, r_time.strftime("%H:%M:%S"), r_notes
            )

            st.session_state.reminders.append(
                {
                    "name": r_name,
                    "form": r_form,
                    "food": r_food,
                    "time": r_time,
                    "notes": r_notes,
                    "id": reminder_id,
                }
            )

            log_activity(f"Reminder added: {r_name}", "Reminder", r_time.strftime("%I:%M %p"))
            add_notification(f"Medication reminder added for {r_time.strftime('%I:%M %p')}", "success")
            st.toast(f"⏰ Reminder added for {r_name}.", icon="⏰")
            st.rerun()

    if st.session_state.reminders:

        now_minutes = (
            datetime.datetime.now().hour * 60
            + datetime.datetime.now().minute
        )

        sorted_reminders = sorted(
            st.session_state.reminders,
            key=lambda r: r["time"]
        )

        for reminder in sorted_reminders:

            r_minutes = (
                reminder["time"].hour * 60
                + reminder["time"].minute
            )

            is_due = abs(r_minutes - now_minutes) <= 30

            css_class = (
                "reminder-card reminder-due"
                if is_due
                else "reminder-card"
            )

            due_text = "Due now" if is_due else ""

            st.markdown(
                f"""
                <div class="{css_class}">
                    <b>{safe_text(reminder['name'])}</b>
                    ({safe_text(reminder['form'])}) —
                    {reminder['time'].strftime('%I:%M %p')}<br>
                    {safe_text(reminder['food'])}
                    {f"<br><i>{safe_text(reminder['notes'])}</i>" if reminder['notes'] else ""}
                    {f"<br><b>{safe_text(due_text)}</b>" if due_text else ""}
                </div>
                """,
                unsafe_allow_html=True
            )

        if st.button("Clear All Reminders"):
            delete_all_reminders()
            st.session_state.reminders = []
            st.rerun()

    else:

        st.info("No reminders added yet.")

# ============================================================
# TAB 5 — Document Upload
# ============================================================

if active_feature == "Documents":

    st.header(T["tab_documents"])

    uploaded_files = st.file_uploader(
        T["upload_docs"],
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="medical_document_uploader",
    )

    if uploaded_files:
        for f in uploaded_files:
            if f.name not in [d["name"] for d in st.session_state.documents]:
                file_data = f.getvalue()
                if len(file_data) > MAX_DOCUMENT_BYTES:
                    st.error(
                        f"{f.name} is too large; the maximum is 10 MiB."
                    )
                    continue
                file_type = getattr(f, "type", None) or "file"
                document_id = save_document(f.name, file_type, len(file_data))
                st.session_state.documents.append({
                    "id": document_id,
                    "name": f.name,
                    "file_type": file_type,
                    "size_kb": round(len(file_data) / 1024, 1),
                    "uploaded_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                })

    st.caption(
        "Only the file's name, type and size are stored. The contents "
        "themselves are neither saved nor read by this app."
    )

    if st.session_state.documents:
        st.subheader("📄 Uploaded Documents")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Name": document["name"],
                        "Type": document.get("file_type") or "file",
                        "Size (KB)": document.get("size_kb", 0),
                        "Uploaded": document.get("uploaded_on", ""),
                    }
                    for document in st.session_state.documents
                ]
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No documents uploaded yet.")

# ============================================================
# TAB 6 — Past History (per logged-in user, from the database)
# ============================================================

if active_feature == "History":

    st.header(T["tab_history"])

    conn = get_connection()
    history_df = pd.read_sql_query(
        """
        SELECT
            created_at AS timestamp,
            symptoms,
            age,
            severity,
            duration,
            predicted_urgency
        FROM triage_history
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        conn,
        params=(st.session_state.user_id,)
    )
    conn.close()

    if history_df.empty:
        st.info(T["history_empty"])
    else:
        st.dataframe(history_df, width="stretch", hide_index=True)

        csv_bytes = history_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "⬇️ Download History as CSV",
            data=csv_bytes,
            file_name="mediscan_history.csv",
            mime="text/csv"
        )
