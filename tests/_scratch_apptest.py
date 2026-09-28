"""Manual end-to-end check of the new features using Streamlit's AppTest.

Not part of the test suite: it talks to the real public drug APIs and to the
real Groq model, so it is a slow, network-dependent smoke test.
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from streamlit.testing.v1 import AppTest

# The real secrets file is moved aside while this local-login smoke test runs,
# so re-inject just the Groq key to exercise the assistant as well.  The key is
# never printed.
SECRETS_BAK = BASE / ".streamlit" / "secrets.toml.bak"
if SECRETS_BAK.exists():
    for line in SECRETS_BAK.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("GROQ_API_KEY"):
            os.environ["GROQ_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
            print("GROQ_API_KEY injected from the local secrets file")

DB = BASE / "database" / "mediscan.db"
USERNAME = "smoketest"
PASSWORD = "smoketest1234"

# --- create a throwaway local user so the login gate can be passed ----------
conn = sqlite3.connect(DB)
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT UNIQUE, password_hash TEXT, created_at TIMESTAMP DEFAULT CURRENT_DATE
    )
    """
)
conn.execute("DELETE FROM users WHERE username = ?", (USERNAME,))
conn.execute("DELETE FROM users WHERE email = ?", (f"{USERNAME}@local.test",))
conn.commit()
conn.close()

import app as app_module  # noqa: E402  (only for hash_password)

conn = sqlite3.connect(DB)
conn.execute(
    "INSERT INTO users (username, name, email, password_hash) VALUES (?, ?, ?, ?)",
    (USERNAME, "Smoke Test", f"{USERNAME}@local.test", app_module.hash_password(PASSWORD)),
)
conn.commit()
conn.close()
print("created local test user")

at = AppTest.from_file(str(BASE / "app.py"), default_timeout=120)
at.run()
print("login page rendered; exceptions:", [str(e) for e in at.exception])

at.text_input(key="login_identifier").set_value(USERNAME)
at.text_input(key="login_password").set_value(PASSWORD)
at.button(key="login_btn").click().run()
print("after login exceptions:", [str(e) for e in at.exception])
print("logged in as:", at.session_state.get("username"))

# ---------------------------- Medicine Scanner ----------------------------
at.button(key="nav_Medicine Scanner").click().run()
print("\n--- Medicine Scanner ---")
print("exceptions:", [str(e) for e in at.exception])

at.text_input(key="medicine_manual_query").set_value("Dolo 650")
search_buttons = [b for b in at.button if "Identify" in b.label or "Search" in b.label]
print("search button labels:", [b.label for b in search_buttons])
search_buttons[0].click().run()
print("exceptions:", [str(e) for e in at.exception])

body = "\n".join(m.value for m in at.markdown)
print("lookup state:", {k: v for k, v in (at.session_state.get("medicine_lookup") or {}).items() if k != "result"})
result = (at.session_state.get("medicine_lookup") or {}).get("result") or {}
print("result sources:", result.get("sources"), "| display:", result.get("display_name"))
print("uses present:", bool(result.get("uses")))
print("web links:", [link["label"] for link in result.get("web_links", [])])
for marker in ("Web results for", "Medicine information from", "openFDA", "Search this medicine anywhere", "Google"):
    print(f"  page contains {marker!r}:", marker in body)

# ---------------------------- AI Assistant --------------------------------
at.button(key="nav_AI Assistant").click().run()
print("\n--- AI Assistant ---")
print("exceptions:", [str(e) for e in at.exception])
print("reply language:", at.session_state.get("assistant_language"))
print("language options:", [s.label for s in at.selectbox(key="assistant_language_switch").options])
print("selectbox label:", at.selectbox(key="assistant_language_switch").label)
body = "\n".join(m.value for m in at.markdown)
for marker in ("AI Health Assistant", "General health information only"):
    print(f"  page contains {marker!r}:", marker in body)

at.selectbox(key="assistant_language_switch").select("తెలుగు").run()
print("exceptions:", [str(e) for e in at.exception])
print("reply language now:", at.session_state.get("assistant_language"))
body = "\n".join(m.value for m in at.markdown)
for marker in ("AI ఆరోగ్య సహాయకుడు", "మందుల సమాచారం", "జవాబు"):
    print(f"  page contains {marker!r}:", marker in body)
print("suggestion chips:", [b.label for b in at.button if "Dolo 650" in b.label or "అమ్లత" in b.label])

# Ask one question in Telugu and check the answer comes back in Telugu.
t0 = time.time()
at.chat_input[0].set_value("పారాసెటమాల్ మందిని భోజనం ముందే తీసుకోవాలా లేదా తర్వాత?").run()
print("chat exceptions:", [str(e) for e in at.exception])
messages = at.session_state.get("chat_messages") or []
print("messages:", len(messages), "| %.1fs" % (time.time() - t0))
if len(messages) >= 2:
    print("answer:", messages[-1]["content"][:400])

print("\nDONE")
