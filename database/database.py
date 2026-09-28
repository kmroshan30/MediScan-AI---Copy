import os
import sqlite3
from pathlib import Path

# The database lives next to this module by default.  MEDISCAN_DB_PATH
# overrides that, so the app can point at a mounted volume on a host that
# provides one.  Streamlit Community Cloud has no durable volume, so this only
# helps when self-hosting somewhere with a real disk.
DB_PATH = Path(
    os.environ.get("MEDISCAN_DB_PATH")
    or (Path(__file__).resolve().parent / "mediscan.db")
)


def get_connection():
    # The parent may not exist yet when MEDISCAN_DB_PATH points somewhere new.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cursor, table, column, definition):
    """Add a column to an existing table when an older database lacks it.

    CREATE TABLE IF NOT EXISTS never alters a table that already exists, so
    databases created by an earlier version need their new columns backfilled.
    """
    cursor.execute(f"PRAGMA table_info({table})")
    if column not in [row["name"] for row in cursor.fetchall()]:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # ============================================================
    # USERS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    # Add username to old databases if it doesn't exist
    cursor.execute("PRAGMA table_info(users)")
    columns = [row["name"] for row in cursor.fetchall()]

    if "username" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")

    # Give existing users usernames
    cursor.execute("""
        UPDATE users
        SET username = 'user_' || id
        WHERE username IS NULL OR username = ''
    """)

    # Unique username index
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username
        ON users(username)
    """)

    # Short-lived, one-use tokens for the local password-reset flow.
    # Tokens are stored only as SHA-256 digests, never as usable secrets.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL,
            used_at INTEGER,
            created_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user
        ON password_reset_tokens(user_id, expires_at)
    """)

    # ============================================================
    # TRIAGE HISTORY
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS triage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symptoms TEXT,
            age INTEGER,
            severity TEXT,
            duration TEXT,
            predicted_urgency TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ============================================================
    # SESSIONS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # A signed-in browser is remembered here so that reloading the page signs
    # the visitor back in instead of dropping them at the login screen.
    # token_hash holds the SHA-256 digest of the browser token (the same
    # convention as password_reset_tokens above), never the token itself, and
    # ui_state carries the small amount of interface state worth surviving a
    # reload. Older databases predate these columns.
    ensure_column(cursor, "sessions", "token_hash", "TEXT")
    ensure_column(cursor, "sessions", "ui_state", "TEXT")
    ensure_column(cursor, "sessions", "expires_at", "INTEGER")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_token_hash
        ON sessions(token_hash)
    """)

    # ============================================================
    # EMERGENCY CONTACTS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emergency_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            name TEXT,
            phone TEXT,
            relation TEXT
        )
    """)

    # ============================================================
    # MEDICINE SCANS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS medicine_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medicine_name TEXT,
            ocr_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ============================================================
    # SAVED MEDICINES
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS saved_medicines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medicine_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # A visitor cannot save the same medicine twice.
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_medicines_user_name
        ON saved_medicines(user_id, medicine_name)
    """)

    # ============================================================
    # REMINDERS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medicine_name TEXT,
            form TEXT,
            food_timing TEXT,
            reminder_time TEXT,
            notes TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(cursor, "reminders", "form", "TEXT")
    ensure_column(cursor, "reminders", "is_active", "INTEGER DEFAULT 1")
    # No DEFAULT here: SQLite refuses to add a column whose default is not a
    # constant, and CURRENT_TIMESTAMP is not one. Nothing reads this column.
    ensure_column(cursor, "reminders", "created_at", "TIMESTAMP")

    # ============================================================
    # DOCUMENTS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            filename TEXT,
            file_type TEXT,
            file_path TEXT,
            size_bytes INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    ensure_column(cursor, "documents", "size_bytes", "INTEGER")

    # ============================================================
    # CHAT HISTORY
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("MediScan database initialized successfully!")