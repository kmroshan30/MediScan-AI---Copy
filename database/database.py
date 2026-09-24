import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "mediscan.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    # REMINDERS
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medicine_name TEXT,
            reminder_time TEXT,
            food_timing TEXT,
            notes TEXT
        )
    """)

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
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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