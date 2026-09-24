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
from pathlib import Path
from urllib.parse import quote

from database.database import init_db, get_connection

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
    layout="wide"
,
    initial_sidebar_state="expanded"
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

# ============================================================
# Database
# ============================================================

init_db()

def hash_password(password: str) -> str:
    # NOTE: plain SHA-256 has no per-user salt. Fine for a coursework
    # prototype; a real deployment should use bcrypt/passlib instead.
    return hashlib.sha256(password.encode()).hexdigest()


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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
if GROQ_API_KEY is None:
    try:
        GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
    except Exception:
        GROQ_API_KEY = None

GROQ_MODEL = "openai/gpt-oss-20b"  # verify against Groq's current model list

ASSISTANT_SYSTEM_PROMPT = (
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
                conn = get_connection()
                cursor = conn.cursor()

                try:
                    cursor.execute(
                        """
                        INSERT INTO users (username, name, email, password_hash)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            reg_username.strip().lower(),
                            reg_name.strip(),
                            reg_email.strip().lower(),
                            hash_password(reg_password)
                        )
                    )
                    conn.commit()
                    conn.close()

                    st.success("Account created successfully. You can now login.")
                    st.session_state.auth_page = "login"
                    st.rerun()

                except sqlite3.IntegrityError:
                    conn.close()
                    st.error("Username or email is already registered.")

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
                SELECT id, username, name
                FROM users
                WHERE (username = ? OR email = ?)
                  AND password_hash = ?
                """,
                (
                    identifier,
                    identifier,
                    hash_password(login_password)
                )
            )

            user = cursor.fetchone()
            conn.close()

            if user:
                st.session_state.user_id = user["id"]
                st.session_state.user_name = user["name"]
                st.session_state.username = user["username"]

                conn = get_connection()
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT name, phone, relation
                    FROM emergency_contacts
                    WHERE user_id = ?
                    """,
                    (user["id"],)
                )

                contact = cursor.fetchone()
                conn.close()

                st.session_state.emergency_contact = (
                    {
                        "name": contact["name"],
                        "phone": contact["phone"],
                        "relation": contact["relation"]
                    }
                    if contact
                    else {"name": "", "phone": "", "relation": ""}
                )

                st.session_state.active_feature = "Home"
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
                    conn = get_connection()
                    cursor = conn.cursor()

                    cursor.execute(
                        "SELECT id FROM users WHERE email = ?",
                        (forgot_email.strip().lower(),)
                    )

                    user = cursor.fetchone()
                    conn.close()

                    if user:
                        st.session_state.reset_email = forgot_email.strip().lower()
                        st.session_state.reset_code = "123456"
                        st.session_state.reset_verified = False
                        st.success("Prototype reset code: 123456")
                    else:
                        st.error("No account found with this email.")

            if st.session_state.get("reset_code"):
                entered_code = st.text_input(
                    "Enter Reset Code",
                    key="reset_code_input"
                )

                if st.button(
                    "Verify Code",
                    key="verify_reset_code"
                ):
                    if entered_code == st.session_state.reset_code:
                        st.session_state.reset_verified = True
                        st.success("Code verified.")
                    else:
                        st.error("Invalid reset code.")

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
                            WHERE email = ?
                            """,
                            (
                                hash_password(new_password),
                                st.session_state.reset_email
                            )
                        )

                        conn.commit()
                        conn.close()

                        st.success("Password reset successfully.")

                        st.session_state.pop("reset_code", None)
                        st.session_state.pop("reset_email", None)
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
# Session state (feature data — separate from auth state above)
# ============================================================

if "documents" not in st.session_state:
    st.session_state.documents = []           # uploaded document metadata (session-only)

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []        # AI assistant conversation (session-only)

if "reminders" not in st.session_state:
    st.session_state.reminders = []            # medication reminders (session-only)

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
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ============================================================
# Sidebar — Account, Language, Emergency Contact
# ============================================================

with st.sidebar:

    # -------------------- Account --------------------
    st.header("Account")
    st.write(f"Logged in as **{st.session_state.user_name}**")

    if st.button("Logout"):
        st.session_state.user_id = None
        st.session_state.user_name = None
        st.session_state.username = None
        st.session_state.emergency_contact = {"name": "", "phone": "", "relation": ""}
        st.rerun()

    with st.expander("Account Settings"):

        st.caption(f"Current username: {st.session_state.username}")

        new_username = st.text_input("New Username", key="new_username")
        if st.button("Update Username", key="update_username"):
            cleaned = new_username.strip().lower()
            if not cleaned:
                st.error("Username cannot be empty.")
            elif len(cleaned) < 3:
                st.error("Username must be at least 3 characters.")
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

                if user and user["password_hash"] == hash_password(current_password):
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
    language = st.selectbox("Language", list(TRANSLATIONS.keys()))
    T = TRANSLATIONS[language]

    st.divider()

    # -------------------- Emergency Contact --------------------
    st.subheader(T["emergency_contact"].replace("", ""))

    with st.form("emergency_contact_form"):
        ec_name = st.text_input("Contact Name", value=st.session_state.emergency_contact["name"])
        ec_phone = st.text_input("Contact Phone", value=st.session_state.emergency_contact["phone"])
        ec_relation = st.text_input("Relation", value=st.session_state.emergency_contact["relation"])
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
        st.markdown(
            f"""
            <div class="emergency-box">
            <b>Your contact:</b> {st.session_state.emergency_contact['name']}
            ({st.session_state.emergency_contact['relation']})<br>
            <a href="tel:{st.session_state.emergency_contact['phone']}">
            {st.session_state.emergency_contact['phone']}</a>
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

try:
    model = joblib.load(MODEL_PATH)
except Exception as e:
    st.error(f"Unable to load the triage model: {e}")
    st.stop()

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
                <div class='ms-hero-actions'><span class='ms-hero-primary'>Start Health Check <b>→</b></span><span class='ms-hero-secondary'>Learn More</span></div>
            </div>
            <div class='ms-home-doctor'></div>
            <div class='ms-home-trust'><b>✓</b><div><strong>Trusted</strong><br><span>AI Healthcare Support</span></div></div>
        </div>""",
        unsafe_allow_html=True
    )

    st.markdown("<div class='ms-section-title'><h3>How can we help you today?</h3><p>Choose a health tool to get started.</p></div>", unsafe_allow_html=True)

    actions = [
        ("♧", "Symptom Triage", "Check symptoms with AI", "Triage", "blue"),
        ("◉", "Medicine Scanner", "Scan & identify medicines", "Medicine Scanner", "coral"),
        ("✦", "AI Assistant", "Ask health-related questions", "AI Assistant", "cyan"),
        ("⌖", "Hospital Finder", "Find nearby hospitals", "Hospital Finder", "green"),
        ("◷", "Reminders", "Manage your reminders", "Reminders", "amber"),
        ("▤", "History", "View past records", "History", "violet"),
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
        ("▣", "Reminders", reminder_count, "medications scheduled", "amber"),
        ("▤", "Recent records", recent_count, "activity and assessments", "blue"),
        ("♥", "Health status", safe_text(status), "from latest triage", "green"),
    ]
    for col, (icon, label, value, sub, tone) in zip(stats, stat_data):
        with col:
            st.markdown(f"<div class='ms-stat-card {tone}'><div class='ms-stat-icon'>{icon}</div><span>{label}</span><strong>{value}</strong><small>{sub}</small></div>", unsafe_allow_html=True)

    st.markdown("<div class='ms-section-title ms-home-section-gap'><h3>Recent Activity</h3></div>", unsafe_allow_html=True)
    activity_items = []
    for _, row in recent_triage.iterrows():
        activity_items.append(("♧", "Symptom assessment", row.get("created_at", ""), row.get("predicted_urgency", "")))
    activity_items += [("•", x["title"], x["time"], x["detail"]) for x in st.session_state.activity_log[:5]]
    if activity_items:
        for icon, title, when, detail in activity_items[:5]:
            st.markdown(f"<div class='ms-activity-row'><div class='ms-activity-icon'>{icon}</div><div class='ms-activity-main'><strong>{safe_text(title)}</strong><span>{safe_text(detail)}</span></div><small>{safe_text(when)}</small></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>◷</div><strong>No recent activity</strong><p>Your assessments, scans and uploads will appear here.</p></div>", unsafe_allow_html=True)

    st.markdown("<div class='ms-health-tip'><div class='ms-tip-icon ms-pulse-icon'>✦</div><div><strong>Health Tips for You</strong><p>Drink enough water, maintain a balanced diet, get regular exercise, and prioritize good sleep for a healthier life.</p></div><span class='ms-tip-arrow'>→</span></div>", unsafe_allow_html=True)

# ============================================================
# PAGE NAVIGATION
# ============================================================

# The app now behaves like separate pages instead of rendering
# every feature beside the others.
active_feature = st.session_state.get("active_feature", "Home")

with st.sidebar:
    st.markdown(
        """<div class='ms-sidebar-brand'>
            <div class='ms-sidebar-logo'>✚</div>
            <div><strong>MediScan AI</strong><span>Smarter Healthcare for Everyone</span></div>
        </div>
        <div class='ms-sidebar-search'>⌕&nbsp;&nbsp; Search...</div>""",
        unsafe_allow_html=True
    )

    st.markdown(
        """<div class='ms-live-status'><span class='ms-status-pulse'></span><span>AI services ready</span><span class='ms-status-dots'><i></i><i></i><i></i></span></div>""",
        unsafe_allow_html=True
    )

    nav_items = [
        ("⌂", "Home"), ("⌕", "Search"), ("♧", "Triage"), ("◉", "Medicine Scanner"), ("✦", "AI Assistant"),
        ("⌖", "Hospital Finder"), ("◷", "Reminders"), ("▤", "History"), ("▧", "Documents"),
        ("♙", "Profile"), ("♢", "Notifications"), ("◇", "Privacy & Security"), ("Aa", "Accessibility")
    ]

    for icon, page_name in nav_items:
        if st.button(f"{icon}  {page_name}", key=f"nav_{page_name}", use_container_width=True):
            st.session_state.active_feature = page_name
            st.rerun()

    st.markdown("""<div class='ms-sidebar-emergency'><div class='ms-emergency-icon'>☎</div><div><strong>Emergency</strong><span>Call 108</span></div></div>""", unsafe_allow_html=True)

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

    _now_key = datetime.datetime.now().strftime("%H:%M")
    for _rem in st.session_state.reminders:
        _rem_key = _rem["time"].strftime("%H:%M")
        if _rem_key == _now_key and _rem_key not in st.session_state.reminder_alerts_fired:
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
            st.session_state.reminder_alerts_fired.add(_rem_key)

    _reminders_js = json.dumps(
        [
            {
                "t": r["time"].strftime("%H:%M"),
                "name": r["name"],
            }
            for r in st.session_state.reminders
        ]
    )

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
            function dueInfo(hm) {
                var list = [];
                reminders.forEach(function (r) { if (r.t === hm) list.push(r.name); });
                return list;
            }
            function render(hm) {
                var dl = dueInfo(hm);
                if (dl.length) {
                    strip.className = "due";
                    strip.innerHTML = "🔔 TIME TO TAKE YOUR MEDICINE — <b>" + dl.join(", ") + "</b>";
                    return;
                }
                var next = null;
                reminders.forEach(function (r) { if (r.t > hm && (!next || r.t < next.t)) next = r; });
                if (next) {
                    strip.className = "";
                    strip.innerHTML = "⏰ Next reminder: <b>" + next.name + "</b> at " + fmt(next.t);
                } else if (reminders.length) {
                    strip.className = "";
                    strip.innerHTML = "⏰ " + reminders.length + " reminder" +
                        (reminders.length > 1 ? "s" : "") + " set for today — you'll be alerted when one is due.";
                } else {
                    strip.style.display = "none";
                }
            }
            function check() {
                var d = new Date();
                var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
                var dl = dueInfo(hm);
                if (dl.length && !fired[hm]) {
                    fired[hm] = true;
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
            st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>⌕</div><strong>No results found</strong><p>Try a medicine name, hospital, symptom or report name.</p></div>", unsafe_allow_html=True)

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
            st.session_state.user_id = None; st.session_state.user_name = None; st.session_state.username = None; st.rerun()

# ============================================================
# NOTIFICATIONS
# ============================================================
if active_feature == "Notifications":
    st.markdown("<div class='ms-page-enter'><h2>Notifications</h2><p class='ms-page-subtitle'>Important updates and reminders.</p></div>", unsafe_allow_html=True)
    if not st.session_state.notifications:
        st.markdown("<div class='ms-empty-state'><div class='ms-empty-icon'>⌁</div><strong>You're all caught up</strong><p>New reminders and important activity will appear here.</p></div>", unsafe_allow_html=True)
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
    st.markdown("<div class='ms-privacy-grid'><div class='ms-privacy-card'><strong>Account protected</strong><span>Your login is stored in the local application database.</span></div><div class='ms-privacy-card'><strong>Health records</strong><span>Your triage history is associated with your account.</span></div><div class='ms-privacy-card'><strong>Documents</strong><span>Uploaded documents in this prototype are session-only.</span></div></div>", unsafe_allow_html=True)
    st.markdown("### Manage Data")
    if st.button("Clear session documents & activity"):
        st.session_state.documents = []; st.session_state.activity_log = []; st.success("Session data cleared.")
    st.markdown("### Delete Account")
    confirm = st.checkbox("I understand that deleting my account removes my saved account records.")
    if st.button("Delete my account", type="secondary", disabled=not confirm):
        conn = get_connection(); cur = conn.cursor()
        for table in ["triage_history", "emergency_contacts", "medicine_scans", "reminders", "documents", "chat_history", "sessions"]:
            try: cur.execute(f"DELETE FROM {table} WHERE user_id = ?", (st.session_state.user_id,))
            except Exception: pass
        cur.execute("DELETE FROM users WHERE id = ?", (st.session_state.user_id,)); conn.commit(); conn.close()
        st.session_state.user_id = None; st.session_state.user_name = None; st.session_state.username = None
        st.rerun()
    st.caption("This is a coursework prototype. For real deployment, use strong password hashing, encrypted storage, access controls and a reviewed privacy policy.")

# ============================================================
# ACCESSIBILITY
# ============================================================
if active_feature == "Accessibility":
    st.markdown("<div class='ms-page-enter'><h2>Accessibility</h2><p class='ms-page-subtitle'>Adjust the interface for easier reading and navigation.</p></div>", unsafe_allow_html=True)
    st.session_state.accessibility_large_text = st.toggle("Larger text", value=st.session_state.accessibility_large_text)
    st.session_state.accessibility_high_contrast = st.toggle("High contrast", value=st.session_state.accessibility_high_contrast)
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
            height=120
        )

        age = st.number_input(T["age"], min_value=1, max_value=120, value=22)

        duration = st.text_input(
            T["duration_q"],
            placeholder="Example: 3 days"
        )

        severity = st.selectbox(T["severity_q"], ["Mild", "Moderate", "Severe"])

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

                        # ---- Save this check to the database ----
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
            placeholder="Choose a city..."
        )

        city_data = price_data[price_data["city"] == selected_city]

        # -------- Revenue District (only shown when a city has more than one) --------
        available_regions = sorted(city_data["region"].dropna().unique())

        if len(available_regions) > 1:
            selected_region = st.selectbox(
                T["select_region"],
                available_regions,
                index=0,
                placeholder="Choose a revenue district..."
            )
            region_data = city_data[city_data["region"] == selected_region]
        else:
            region_data = city_data

        # -------- District / Mandal --------
        selected_district = st.selectbox(
            T["select_district"],
            sorted(region_data["district"].dropna().unique()),
            index=0,
            placeholder="Choose a district / mandal..."
        )

        # -------- Procedure (search + select) --------
        all_procedures = sorted(region_data["procedure"].dropna().unique())

        procedure_search = st.text_input(
            "🔍 Search Medical Test / Procedure",
            placeholder="Type to search — e.g. 'MRI', 'Blood', 'Scan'..."
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
            placeholder="Choose a medical test or procedure..."
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
            "This looks up medicines in a small demo database "
            f"({len(medicines_df)} entries) — it will not recognize medicines "
            "outside that list, and OCR can misread a blurry or angled photo. "
            "Always double-check with a pharmacist before taking anything."
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
                photo = st.camera_input("Take a photo of the medicine strip/box")
                uploaded_photo = st.file_uploader(
                    "...or upload a photo instead", type=["png", "jpg", "jpeg"]
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
            manual_query = st.text_input(
                "Medicine name",
                placeholder="e.g. Paracetamol, Dolo 650, Amoxicillin..."
            )
            if st.button(T["scan_medicine_btn"]) and manual_query.strip():
                matched_medicine = find_medicine(manual_query, medicines_df)
                if matched_medicine is None:
                    st.warning("No close match found in the demo database.")

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
                        st.session_state.saved_medicines.append(med_name)
                        log_activity(f"{med_name} saved", "Medicine", "Saved medicine")
                        add_notification("Medicine saved successfully", "success")
                    st.success("Medicine saved to your list.")

# ============================================================
# TAB 4 — AI Assistant & Medication Reminders
# ============================================================

if active_feature == "AI Assistant":

    st.markdown(
        """
        <div class="ai-page-header">
            <div class="ai-icon">AI</div>
            <div>
                <h2>AI Health Assistant</h2>
                <p>Ask general health questions and get clear, cautious information.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown(
        """
        <div class="ai-disclaimer">
            General health information only — not a diagnosis or a substitute
            for professional medical advice.
        </div>
        """,
        unsafe_allow_html=True
    )

    if not GROQ_LIB_AVAILABLE:
        st.warning("The `groq` package isn't installed. Run `pip install groq`.")
    elif GROQ_API_KEY is None:
        st.warning(
            "No Groq API key found. Set `GROQ_API_KEY` in your environment "
            "or `.streamlit/secrets.toml`."
        )
    else:

        if not st.session_state.chat_messages:

            st.markdown(
                """
                <div class="ai-welcome-card">
                    <div class="ai-welcome-icon">AI</div>
                    <h3>Hello, I'm your MediScan AI assistant.</h3>
                    <p>
                        Ask me about medicines, symptoms, health habits,
                        or general healthcare information.
                    </p>
                    <div class="ai-suggestions">
                        <span>Medicine information</span>
                        <span>Symptom information</span>
                        <span>Healthy habits</span>
                    </div>
                    <div class="ai-ready-animation">
                        <span>Ready to help</span>
                        <span class="ms-status-dots"><i></i><i></i><i></i></span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

        control_cols = st.columns(5)
        with control_cols[0]:
            if st.button("Clear chat", use_container_width=True):
                st.session_state.chat_messages = []
                st.rerun()
        with control_cols[1]:
            if st.button("Explain simply", use_container_width=True):
                st.session_state.ai_prefill = "Explain your last answer in very simple words."
        with control_cols[2]:
            if st.button("Summarize", use_container_width=True):
                st.session_state.ai_prefill = "Summarize your last answer in 3 short bullet points."
        with control_cols[3]:
            if st.button("Regenerate", use_container_width=True) and st.session_state.chat_messages:
                if st.session_state.chat_messages[-1]["role"] == "assistant":
                    st.session_state.chat_messages.pop()
                st.session_state.ai_regenerate = True
                st.rerun()
        with control_cols[4]:
            st.caption("Voice input")
            st.audio_input("Record", key="ai_voice_input")

        chat_box = st.container()

        with chat_box:

            for msg in st.session_state.chat_messages:

                if msg["role"] == "user":

                    st.markdown(
                        f"""
                        <div class="ai-message ai-user-message">
                            <div class="ai-message-label">You</div>
                            <div>{msg["content"]}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                else:

                    st.markdown(
                        f"""
                        <div class="ai-message ai-assistant-message">
                            <div class="ai-message-label">MediScan AI</div>
                            <div>{msg["content"]}</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

        prefill = st.session_state.pop("ai_prefill", "")
        if prefill:
            st.info(prefill)
        user_question = st.chat_input("Type your health question here...")
        if st.session_state.pop("ai_regenerate", False):
            user_question = "Please regenerate your previous answer with clearer wording."

        if user_question:

            st.session_state.chat_messages.append(
                {
                    "role": "user",
                    "content": user_question
                }
            )

            try:

                client = Groq(
                    api_key=GROQ_API_KEY
                )

                ai_loading = st.empty()
                ai_loading.markdown(
                    """
                    <div class="ms-inline-loader ms-ai-loader">
                        <div class="ms-loading-content">
                            <div class="ms-spinner"></div>
                            <p>MediScan AI is thinking...</p>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                response = client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=(
                            [
                                {
                                    "role": "system",
                                    "content": ASSISTANT_SYSTEM_PROMPT
                                }
                            ]
                            + st.session_state.chat_messages
                        ),
                        max_tokens=300,
                        temperature=0.4
                    )

                ai_loading.empty()
                reply = response.choices[0].message.content

            except Exception as e:

                reply = f"Assistant error: {e}"

            st.session_state.chat_messages.append(
                {
                    "role": "assistant",
                    "content": reply
                }
            )

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

        r_name = st.text_input("Medicine name", value=st.session_state.pop("prefill_reminder", ""))

        r_form = st.selectbox(
            "Form",
            [
                "Tablet",
                "Capsule",
                "Syrup",
                "Liquid",
                "Injection",
                "Other"
            ]
        )

        r_food = st.selectbox(
            "Timing",
            [
                "Before Food",
                "After Food",
                "Either / Not applicable"
            ]
        )

        r_time = st.time_input(
            "Time to take it",
            value=datetime.time(9, 0)
        )

        r_notes = st.text_input(
            "Notes (optional)",
            placeholder="e.g. twice daily, with water"
        )

        add_clicked = st.form_submit_button(
            T["add_reminder_btn"]
        )

        if add_clicked and r_name.strip():

            st.session_state.reminders.append(
                {
                    "name": r_name,
                    "form": r_form,
                    "food": r_food,
                    "time": r_time,
                    "notes": r_notes
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
                    <b>{reminder['name']}</b>
                    ({reminder['form']}) —
                    {reminder['time'].strftime('%I:%M %p')}<br>
                    {reminder['food']}
                    {f"<br><i>{reminder['notes']}</i>" if reminder['notes'] else ""}
                    {f"<br><b>{due_text}</b>" if due_text else ""}
                </div>
                """,
                unsafe_allow_html=True
            )

        if st.button("Clear All Reminders"):

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
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        for f in uploaded_files:
            if f.name not in [d["name"] for d in st.session_state.documents]:
                st.session_state.documents.append({
                    "name": f.name,
                    "size_kb": round(len(f.getvalue()) / 1024, 1),
                    "uploaded_on": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                })

    st.caption(
        "Note: this prototype only stores your documents for this browser "
        "session — it does not read or extract data from them (no OCR)."
    )

    if st.session_state.documents:
        st.subheader("📄 Uploaded Documents")
        st.dataframe(pd.DataFrame(st.session_state.documents), width="stretch", hide_index=True)
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