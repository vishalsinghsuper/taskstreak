import datetime
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import streamlit as st

try:
    import psycopg2
    from psycopg2 import pool
except ImportError:
    psycopg2 = None
    pool = None


about_text = """
**About StreakForge**

StreakForge is a gamified productivity engine designed to forge unbreakable
discipline. Built on an "All-or-Nothing" accountability system, it helps you
execute daily requirements while tracking progress across the different pillars
of your life.

**Core Features:**
* **The Master Forge:** A unified habit tracker enforcing daily discipline.
* **Shadow Streaks:** Granular analytics tracking Iron, Mind, and General progress.
* **The Event Board:** A tactical space for strict deadlines and timeless goals.

Forged by: **Vishal Kumar Singh**
"""

PILLARS = {
    "Iron": {"label": "💪 Iron", "stat": "stats_iron", "class": "iron"},
    "Mind": {"label": "🧠 Mind", "stat": "stats_mind", "class": "mind"},
    "General": {"label": "📊 General", "stat": "stats_general", "class": "general"},
}

USER_STORE = Path(__file__).with_name("streakforge_users.json")
DB_PATH = Path(__file__).with_name("streakforge.db")
APP_STATE_KEYS = [
    "habits",
    "active_pillar",
    "stats_master",
    "stats_iron",
    "stats_mind",
    "stats_general",
    "events",
    "history",
    "notes_list",
]


st.set_page_config(
    page_title="StreakForge",
    page_icon="🔥",
    layout="wide",
    menu_items={
        "Get Help": None,
        "Report a bug": "mailto:vishal.singh.cb24@ggits.net?subject=StreakForge%20Bug%20Report",
        "About": about_text,
    },
)


def init_state(name, default_val):
    if name not in st.session_state:
        st.session_state[name] = default_val


def get_config_value(name):
    if os.getenv(name):
        return os.getenv(name)
    try:
        return st.secrets.get(name)
    except Exception:
        return None


def get_database_url():
    return get_config_value("SUPABASE_DB_URL") or get_config_value("DATABASE_URL")


def using_postgres():
    return bool(get_database_url())


def db_placeholder():
    return "%s" if using_postgres() else "?"


@st.cache_resource(show_spinner=False)
def get_postgres_pool(database_url):
    if psycopg2 is None or pool is None:
        raise RuntimeError("Install psycopg2-binary to use Supabase/Postgres.")
    return pool.SimpleConnectionPool(1, 5, database_url, sslmode="require")


@contextmanager
def get_db():
    database_url = get_database_url()
    if database_url:
        postgres_pool = get_postgres_pool(database_url)
        conn = postgres_pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            postgres_pool.putconn(conn)
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def db_execute(sql, params=()):
    with get_db() as conn:
        if using_postgres():
            with conn.cursor() as cur:
                cur.execute(sql, params)
        else:
            conn.execute(sql, params)


def db_fetchall(sql, params=()):
    with get_db() as conn:
        if using_postgres():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        return conn.execute(sql, params).fetchall()


def db_fetchone(sql, params=()):
    with get_db() as conn:
        if using_postgres():
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        return conn.execute(sql, params).fetchone()


@st.cache_resource(show_spinner=False)
def ensure_database_ready(database_key):
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    db_execute(
        """
        CREATE TABLE IF NOT EXISTS user_state (
            username TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
        )
        """
    )

    migrate_json_users_to_db()
    return True


def init_database():
    ensure_database_ready(get_database_url() or str(DB_PATH))


def migrate_json_users_to_db():
    if not USER_STORE.exists():
        return

    try:
        with USER_STORE.open("r", encoding="utf-8") as file:
            old_users = json.load(file)
    except (json.JSONDecodeError, OSError):
        return

    placeholder = db_placeholder()
    sql = (
        """
        INSERT INTO users (username, display_name, email, password, created_at)
        VALUES ({0}, {0}, {0}, {0}, {0})
        ON CONFLICT(username) DO NOTHING
        """
    ).format(placeholder)
    for username, user in old_users.items():
        db_execute(
            sql,
            (
                username,
                user.get("display_name", username),
                user.get("email", ""),
                user.get("password", ""),
                user.get("created_at", datetime.datetime.now().isoformat(timespec="seconds")),
            ),
        )


def load_users():
    init_database()
    rows = db_fetchall("SELECT username, display_name, email, password, created_at FROM users ORDER BY username")

    return {
        username: {
            "display_name": display_name,
            "email": email,
            "password": password,
            "created_at": created_at,
        }
        for username, display_name, email, password, created_at in rows
    }


def save_users(users):
    init_database()
    placeholder = db_placeholder()
    sql = (
        """
        INSERT INTO users (username, display_name, email, password, created_at)
        VALUES ({0}, {0}, {0}, {0}, {0})
        ON CONFLICT(username) DO UPDATE SET
            display_name = excluded.display_name,
            email = excluded.email,
            password = excluded.password,
            created_at = excluded.created_at
        """
    ).format(placeholder)
    for username, user in users.items():
        db_execute(
            sql,
            (
                username,
                user["display_name"],
                user["email"],
                user["password"],
                user["created_at"],
            ),
        )


def default_user_state():
    return {
        "habits": [],
        "active_pillar": "General",
        "stats_master": {"current": 0, "prev": 0, "best": 0},
        "stats_iron": {"current": 0, "prev": 0, "best": 0},
        "stats_mind": {"current": 0, "prev": 0, "best": 0},
        "stats_general": {"current": 0, "prev": 0, "best": 0},
        "events": [],
        "history": [],
        "notes_list": [],
    }


def serialize_event(event):
    serialized = dict(event)
    for key in ("deadline", "done_date"):
        if isinstance(serialized.get(key), (datetime.date, datetime.datetime)):
            serialized[key] = serialized[key].isoformat()
    return serialized


def deserialize_event(event):
    deserialized = dict(event)
    for key in ("deadline", "done_date"):
        value = deserialized.get(key)
        if value:
            deserialized[key] = datetime.date.fromisoformat(value)
        else:
            deserialized[key] = None
    return deserialized


def collect_user_state():
    return {
        "habits": st.session_state.habits,
        "active_pillar": st.session_state.active_pillar,
        "stats_master": st.session_state.stats_master,
        "stats_iron": st.session_state.stats_iron,
        "stats_mind": st.session_state.stats_mind,
        "stats_general": st.session_state.stats_general,
        "events": [serialize_event(event) for event in st.session_state.events],
        "history": [serialize_event(event) for event in st.session_state.history],
        "notes_list": st.session_state.notes_list,
    }


def save_user_state(username, data):
    init_database()
    placeholder = db_placeholder()
    sql = (
        """
        INSERT INTO user_state (username, data_json, updated_at)
        VALUES ({0}, {0}, {0})
        ON CONFLICT(username) DO UPDATE SET
            data_json = excluded.data_json,
            updated_at = excluded.updated_at
        """
    ).format(placeholder)
    db_execute(
        sql,
        (
            username,
            json.dumps(data),
            datetime.datetime.now().isoformat(timespec="seconds"),
        ),
    )


def save_current_user_state():
    if st.session_state.get("authenticated") and st.session_state.get("current_user"):
        save_user_state(st.session_state.current_user, collect_user_state())


def load_user_state(username):
    init_database()
    row = db_fetchone(f"SELECT data_json FROM user_state WHERE username = {db_placeholder()}", (username,))

    if not row:
        return default_user_state()

    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return default_user_state()

    state = default_user_state()
    state.update(data)
    state["events"] = [deserialize_event(event) for event in state["events"]]
    state["history"] = [deserialize_event(event) for event in state["history"]]
    return state


def apply_user_state(data):
    for key, value in data.items():
        st.session_state[key] = value


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password, stored_password):
    try:
        salt, expected_hash = stored_password.split("$", 1)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return hmac.compare_digest(digest.hex(), expected_hash)


def reset_app_session():
    for key in APP_STATE_KEYS:
        if key in st.session_state:
            del st.session_state[key]
    for key in list(st.session_state.keys()):
        if key.startswith(("hbt_", "evt_", "edit_hbt_", "edit_evt_")):
            del st.session_state[key]


def login_user(username, users):
    st.session_state.authenticated = True
    st.session_state.current_user = username
    st.session_state.current_display_name = users[username]["display_name"]
    reset_app_session()
    apply_user_state(load_user_state(username))
    st.rerun()


def logout_user():
    st.session_state.authenticated = False
    st.session_state.current_user = None
    st.session_state.current_display_name = ""
    reset_app_session()
    st.rerun()


def normalize_pillar(value):
    if value in PILLARS:
        return value
    if "Iron" in str(value):
        return "Iron"
    if "Mind" in str(value):
        return "Mind"
    return "General"


def pillar_label(value):
    return PILLARS[normalize_pillar(value)]["label"]


def can_edit_forge():
    return datetime.datetime.now().time() < datetime.time(22, 0)


def show_after_10_warning(area):
    st.warning(f"{area} edits are locked after 10:00 PM.")


def clear_event_edit_state():
    for key in list(st.session_state.keys()):
        if key.startswith("edit_evt_"):
            del st.session_state[key]


init_state("authenticated", False)
init_state("current_user", None)
init_state("current_display_name", "")
init_state("auth_view", "login")
init_state("habits", [])
init_state("active_pillar", "General")
init_state("stats_master", {"current": 0, "prev": 0, "best": 0})
init_state("stats_iron", {"current": 0, "prev": 0, "best": 0})
init_state("stats_mind", {"current": 0, "prev": 0, "best": 0})
init_state("stats_general", {"current": 0, "prev": 0, "best": 0})
init_state("events", [])
init_state("history", [])
init_state("notes_list", [])

st.session_state.active_pillar = normalize_pillar(st.session_state.active_pillar)
for habit in st.session_state.habits:
    habit["pillar"] = normalize_pillar(habit.get("pillar", "General"))


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        :root {
            --forge-bg: #1a0b2e;
            --forge-panel: rgba(255, 255, 255, 0.055);
            --forge-panel-strong: rgba(255, 255, 255, 0.09);
            --forge-border: rgba(255, 255, 255, 0.13);
            --forge-muted: #a7a0b8;
            --forge-text: #f8fafc;
            --forge-accent: #8b5cf6;
            --forge-accent-2: #6366f1;
            --forge-amber: #f59e0b;
        }

        html, body, [data-testid="stAppViewContainer"] {
            min-height: 100%;
            color: var(--forge-text);
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background:
                radial-gradient(circle at top left, rgba(139, 92, 246, 0.28), transparent 34rem),
                radial-gradient(circle at bottom right, rgba(245, 158, 11, 0.12), transparent 30rem),
                linear-gradient(135deg, #160923 0%, #211039 48%, #12091f 100%);
        }

        [data-testid="stHeader"], [data-testid="stToolbar"] {
            background: transparent;
        }

        [data-testid="stAppViewBlockContainer"] {
            max-width: 1180px;
            padding: 2rem 3rem 4rem;
        }

        [data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.055);
            border-right: 1px solid var(--forge-border);
            backdrop-filter: blur(16px);
        }

        [data-testid="stSidebar"] > div:first-child {
            padding: 2rem 1.35rem;
        }

        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {
            color: var(--forge-text);
        }

        [data-testid="stSidebar"] [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid var(--forge-border);
            border-radius: 14px;
            padding: 0.8rem;
        }

        [data-testid="stMetricValue"] {
            color: #fff;
            font-weight: 800;
        }

        .forge-brand {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            margin-bottom: 2rem;
        }

        .forge-brand-icon {
            display: grid;
            width: 2.5rem;
            height: 2.5rem;
            place-items: center;
            border-radius: 14px;
            background: linear-gradient(135deg, #f97316, #8b5cf6);
            box-shadow: 0 18px 44px rgba(99, 102, 241, 0.28);
            font-size: 1.35rem;
        }

        .forge-brand h1 {
            margin: 0;
            font-size: 1.3rem;
            line-height: 1.1;
            letter-spacing: 0;
        }

        .forge-brand p {
            margin: 0.25rem 0 0;
            color: var(--forge-muted);
            font-size: 0.78rem;
        }

        .profile-row {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.75rem;
            margin: 0.8rem 0 0.65rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.13);
        }

        .profile-avatar {
            display: grid;
            flex: 0 0 auto;
            width: 2.5rem;
            height: 2.5rem;
            place-items: center;
            border-radius: 999px;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.9), rgba(245, 158, 11, 0.82));
            box-shadow: 0 12px 30px rgba(99, 102, 241, 0.28);
            font-size: 1.3rem;
        }

        .profile-name {
            min-width: 0;
            color: #fff;
            font-size: 0.92rem;
            font-weight: 800;
            line-height: 1.2;
            overflow-wrap: anywhere;
        }

        .section-title {
            color: #d8d2e8;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin: 1.6rem 0 0.7rem;
        }

        .glass-panel {
            background: var(--forge-panel);
            border: 1px solid var(--forge-border);
            border-radius: 18px;
            box-shadow: 0 24px 70px rgba(0, 0, 0, 0.2);
            backdrop-filter: blur(14px);
        }

        .auth-shell {
            min-height: calc(100vh - 8rem);
            display: grid;
            grid-template-columns: minmax(0, 0.95fr) minmax(340px, 0.72fr);
            gap: 1.4rem;
            align-items: center;
        }

        .auth-hero {
            padding: 2rem;
        }

        .auth-mark {
            display: inline-grid;
            place-items: center;
            width: 4rem;
            height: 4rem;
            margin-bottom: 1.4rem;
            border-radius: 18px;
            background: linear-gradient(135deg, #f97316, #8b5cf6);
            box-shadow: 0 26px 70px rgba(99, 102, 241, 0.35);
            font-size: 2rem;
        }

        .auth-hero h1 {
            max-width: 40rem;
            margin: 0;
            color: #fff;
            font-size: clamp(2.25rem, 5vw, 4.8rem);
            line-height: 0.96;
            letter-spacing: 0;
        }

        .auth-hero p {
            max-width: 34rem;
            margin: 1rem 0 0;
            color: #c9c2d8;
            font-size: 1.03rem;
            line-height: 1.7;
        }

        .auth-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.7rem;
            margin-top: 1.6rem;
            max-width: 44rem;
        }

        .auth-chip {
            min-height: 6.25rem;
            padding: 0.9rem;
            border-radius: 14px;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.13);
        }

        .auth-chip strong {
            display: block;
            color: #fff;
            font-size: 1.35rem;
            line-height: 1;
        }

        .auth-chip span {
            display: block;
            margin-top: 0.45rem;
            color: var(--forge-muted);
            font-size: 0.78rem;
            line-height: 1.35;
        }

        .auth-card {
            padding: 1.1rem 1.2rem 1.25rem;
        }

        .auth-card h2 {
            margin: 0 0 0.35rem;
            color: #fff;
            font-size: 1.45rem;
            letter-spacing: 0;
        }

        .auth-card p {
            margin: 0 0 0.75rem;
            color: var(--forge-muted);
            font-size: 0.9rem;
        }

        .hero-panel {
            padding: 0.55rem 0.75rem;
            margin-bottom: 0.55rem;
        }

        .hero-panel h1 {
            margin: 0;
            color: #fff;
            font-size: 1.25rem;
            line-height: 1.15;
            letter-spacing: 0;
        }

        .hero-panel p {
            max-width: 48rem;
            color: #c9c2d8;
            margin: 0.2rem 0 0;
            font-size: 0.78rem;
        }

        .stat-row {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.55rem;
            margin: 0.65rem 0 0.15rem;
        }

        .stat-card {
            background: rgba(255, 255, 255, 0.055);
            border: 1px solid var(--forge-border);
            border-radius: 12px;
            padding: 0.62rem 0.7rem;
        }

        .stat-card small {
            display: block;
            color: var(--forge-muted);
            font-size: 0.64rem;
            font-weight: 700;
            text-transform: uppercase;
            margin-bottom: 0.25rem;
        }

        .stat-card strong {
            color: #fff;
            font-size: 1.15rem;
            line-height: 1;
        }

        .empty-forge {
            min-height: 410px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            text-align: center;
            padding: 3rem 1.5rem;
        }

        .empty-orb {
            width: 8rem;
            height: 8rem;
            display: grid;
            place-items: center;
            margin-bottom: 1.8rem;
            border-radius: 999px;
            background: linear-gradient(135deg, #4f46e5, #a78bfa);
            box-shadow: 0 26px 80px rgba(99, 102, 241, 0.35);
            font-size: 3.25rem;
        }

        .empty-forge h2 {
            margin: 0 0 0.7rem;
            color: #fff;
            font-size: 2rem;
            letter-spacing: 0;
        }

        .empty-forge p {
            margin: 0;
            color: var(--forge-muted);
            font-size: 1.05rem;
            line-height: 1.7;
            max-width: 30rem;
        }

        .habit-card {
            display: flex;
            align-items: center;
            gap: 0.9rem;
            padding: 0.95rem 1rem;
            margin-bottom: 0.65rem;
        }

        .habit-card.done .habit-text {
            color: #a7a0b8;
            text-decoration: line-through;
        }

        .habit-pill {
            flex: 0 0 auto;
            border-radius: 999px;
            padding: 0.25rem 0.65rem;
            font-size: 0.74rem;
            font-weight: 800;
            border: 1px solid rgba(255, 255, 255, 0.14);
            background: rgba(255, 255, 255, 0.07);
        }

        .habit-text {
            min-width: 0;
            color: #fff;
            font-weight: 700;
            overflow-wrap: anywhere;
        }

        [data-testid="stTabs"] div[role="tablist"],
        div[data-baseweb="tab-list"] {
            gap: 0.85rem;
            padding: 0.75rem;
            margin: 0.7rem 0 1.25rem;
            background: rgba(255, 255, 255, 0.07);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 18px;
            box-shadow: 0 22px 60px rgba(0, 0, 0, 0.22);
            backdrop-filter: blur(14px);
        }

        [data-testid="stTabs"] button[role="tab"],
        button[data-baseweb="tab"] {
            min-height: 3.75rem;
            color: #d8d2e8;
            background: rgba(255, 255, 255, 0.045);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 14px;
            padding: 0.95rem 1.35rem;
            flex: 1 1 0;
            justify-content: center;
            font-size: 1.25rem;
            font-weight: 950;
            letter-spacing: 0;
            transition: 0.18s ease;
        }

        [data-testid="stTabs"] button[role="tab"] p,
        button[data-baseweb="tab"] p {
            font-size: 1.25rem;
            font-weight: 950;
            line-height: 1.1;
        }

        [data-testid="stTabs"] button[role="tab"]:hover,
        button[data-baseweb="tab"]:hover {
            color: #fff;
            background: rgba(139, 92, 246, 0.18);
            border-color: rgba(255, 255, 255, 0.2);
        }

        [data-testid="stTabs"] button[role="tab"][aria-selected="true"],
        button[data-baseweb="tab"][aria-selected="true"] {
            color: #fff;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.75), rgba(139, 92, 246, 0.62));
            border: 1px solid rgba(255, 255, 255, 0.3);
            box-shadow: 0 16px 40px rgba(99, 102, 241, 0.35);
            text-shadow: 0 1px 18px rgba(255, 255, 255, 0.28);
        }

        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] p,
        button[data-baseweb="tab"][aria-selected="true"] p {
            color: #fff;
        }

        [data-testid="stForm"], [data-testid="stExpander"], [data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255, 255, 255, 0.055);
            border: 1px solid var(--forge-border);
            border-radius: 18px;
            backdrop-filter: blur(14px);
        }

        [data-testid="stForm"] {
            padding: 0.75rem 1rem 1rem;
        }

        [data-testid="stTextInput"] input,
        [data-testid="stTextArea"] textarea,
        [data-testid="stDateInput"] input {
            color: #f8fafc;
            background: rgba(255, 255, 255, 0.06);
            border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 14px;
        }

        [data-testid="stTextInput"] input::placeholder,
        [data-testid="stTextArea"] textarea::placeholder {
            color: #8f86a3;
        }

        .stButton > button,
        [data-testid="stFormSubmitButton"] button {
            color: white;
            background: #4f46e5;
            border: 1px solid rgba(255, 255, 255, 0.14);
            border-radius: 13px;
            font-weight: 800;
            transition: 0.18s ease;
        }

        .stButton > button:hover,
        [data-testid="stFormSubmitButton"] button:hover {
            color: white;
            background: #6366f1;
            border-color: rgba(255, 255, 255, 0.22);
            transform: translateY(-1px);
        }

        [data-testid="stRadio"] label {
            color: #f8fafc;
        }

        [data-testid="stRadio"] label p {
            font-size: 1.18rem;
            font-weight: 800;
            line-height: 1.15;
        }

        [data-testid="stProgress"] > div > div {
            background: rgba(255, 255, 255, 0.09);
        }

        [data-testid="stProgress"] [role="progressbar"] {
            background: linear-gradient(90deg, #8b5cf6, #f59e0b);
        }

        .stAlert {
            background: rgba(255, 255, 255, 0.065);
            border: 1px solid var(--forge-border);
            border-radius: 16px;
        }

        hr {
            border-color: rgba(255, 255, 255, 0.11);
        }

        @media (max-width: 900px) {
            [data-testid="stAppViewBlockContainer"] {
                padding: 1rem;
            }

            .auth-shell {
                min-height: auto;
                grid-template-columns: 1fr;
            }

            .auth-hero {
                padding: 1.2rem 0.5rem;
            }

            .auth-grid {
                grid-template-columns: 1fr;
            }

            .stat-row {
                grid-template-columns: 1fr;
            }

            .empty-orb {
                width: 6.5rem;
                height: 6.5rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def eval_streak(stat_key, is_successful):
    stats = st.session_state[stat_key]
    if is_successful:
        stats["current"] += 1
        if stats["current"] > stats["best"]:
            stats["best"] = stats["current"]
    else:
        stats["prev"] = stats["current"]
        stats["current"] = 0


def process_midnight():
    habits = st.session_state.habits

    iron_habits = [h for h in habits if normalize_pillar(h["pillar"]) == "Iron"]
    mind_habits = [h for h in habits if normalize_pillar(h["pillar"]) == "Mind"]
    general_habits = [h for h in habits if normalize_pillar(h["pillar"]) == "General"]

    master_done = all(h["done"] for h in habits) if habits else False
    iron_done = all(h["done"] for h in iron_habits) if iron_habits else False
    mind_done = all(h["done"] for h in mind_habits) if mind_habits else False
    general_done = all(h["done"] for h in general_habits) if general_habits else False

    if habits:
        eval_streak("stats_master", master_done)
    if iron_habits:
        eval_streak("stats_iron", iron_done)
    if mind_habits:
        eval_streak("stats_mind", mind_done)
    if general_habits:
        eval_streak("stats_general", general_done)

    for i, habit in enumerate(habits):
        habit["done"] = False
        if f"hbt_{i}" in st.session_state:
            st.session_state[f"hbt_{i}"] = False

    active_events = []
    for evt in st.session_state.events:
        if evt["done"]:
            st.session_state.history.append(evt)
            if f"evt_{evt['id']}" in st.session_state:
                del st.session_state[f"evt_{evt['id']}"]
        else:
            active_events.append(evt)

    st.session_state.events = active_events
    save_current_user_state()
    st.toast("Midnight passed. The Forge resets.", icon="🌙")


def reset_forge():
    st.session_state.habits = []
    st.session_state.events = []
    st.session_state.history = []
    st.session_state.stats_master = {"current": 0, "prev": 0, "best": 0}
    st.session_state.stats_iron = {"current": 0, "prev": 0, "best": 0}
    st.session_state.stats_mind = {"current": 0, "prev": 0, "best": 0}
    st.session_state.stats_general = {"current": 0, "prev": 0, "best": 0}
    save_current_user_state()


def render_auth_page():
    left_col, right_col = st.columns([0.58, 0.42], gap="large", vertical_alignment="center")

    with left_col:
        st.markdown(
            """
            <div class="auth-hero">
                <div class="auth-mark">🔥</div>
                <h1>Enter the Forge.</h1>
                <p>Build your day like a quest: sharpen your habits, clear your events, bank your notes, and watch discipline turn into streak power.</p>
                <div class="auth-grid">
                    <div class="auth-chip"><strong>Quest</strong><span>Every task becomes a mission you can finish.</span></div>
                    <div class="auth-chip"><strong>XP</strong><span>Small wins stack into visible momentum.</span></div>
                    <div class="auth-chip"><strong>Focus</strong><span>One calm command center for daily execution.</span></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right_col:
        with st.container(border=True):
            if st.session_state.auth_view == "login":
                st.markdown(
                    """
                    <div class="auth-card">
                        <h2>Login</h2>
                        <p>Welcome back. Your forge is warm.</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.form("login_form"):
                    username = st.text_input("Username", placeholder="your username")
                    password = st.text_input("Password", type="password", placeholder="your password")
                    submitted = st.form_submit_button("Login", use_container_width=True)

                    if submitted:
                        users = load_users()
                        normalized_username = username.strip().lower()
                        if not normalized_username or not password:
                            st.warning("Enter your username and password.")
                        elif normalized_username not in users:
                            st.error("No account found with that username.")
                        elif not verify_password(password, users[normalized_username]["password"]):
                            st.error("Incorrect password.")
                        else:
                            st.success("Login successful.")
                            login_user(normalized_username, users)

                st.markdown("<p style='text-align:center;margin:0.8rem 0 0.4rem;'>New here?</p>", unsafe_allow_html=True)
                if st.button("Create a new account", use_container_width=True):
                    st.session_state.auth_view = "signup"
                    st.rerun()

            else:
                st.markdown(
                    """
                    <div class="auth-card">
                        <h2>Create Account</h2>
                        <p>Claim your forge and start stacking wins.</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.form("signup_form"):
                    display_name = st.text_input("Name", placeholder="Vishal")
                    new_username = st.text_input("Choose Username", placeholder="vishal")
                    email = st.text_input("Email", placeholder="you@example.com")
                    new_password = st.text_input("Create Password", type="password", placeholder="At least 6 characters")
                    confirm_password = st.text_input("Confirm Password", type="password")
                    submitted = st.form_submit_button("Create Account", use_container_width=True)

                    if submitted:
                        users = load_users()
                        normalized_username = new_username.strip().lower()
                        clean_email = email.strip().lower()

                        if not display_name.strip() or not normalized_username or not clean_email or not new_password:
                            st.warning("Fill in all signup fields.")
                        elif not re.fullmatch(r"[a-z0-9_]{3,20}", normalized_username):
                            st.error("Username must be 3-20 characters using lowercase letters, numbers, or underscore.")
                        elif normalized_username in users:
                            st.error("That username is already taken.")
                        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email):
                            st.error("Enter a valid email address.")
                        elif any(user.get("email") == clean_email for user in users.values()):
                            st.error("An account already exists with that email.")
                        elif len(new_password) < 6:
                            st.error("Password must be at least 6 characters.")
                        elif new_password != confirm_password:
                            st.error("Passwords do not match.")
                        else:
                            users[normalized_username] = {
                                "display_name": display_name.strip(),
                                "email": clean_email,
                                "password": hash_password(new_password),
                                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                            }
                            save_users(users)
                            st.success("Account created. Taking you into the Forge.")
                            login_user(normalized_username, users)

                if st.button("Back to login", use_container_width=True):
                    st.session_state.auth_view = "login"
                    st.rerun()


def render_sidebar():
    with st.sidebar:
        st.markdown(
            """
            <div class="forge-brand">
                <div class="forge-brand-icon">🔥</div>
                <div>
                    <h1>StreakForge</h1>
                    <p>Build the discipline.</p>
                </div>
            </div>
            <div class="section-title">The Ledger</div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("#### 🔥 Master Streak")
        st.metric("Current Streak", f"{st.session_state.stats_master['current']} Days")

        prev_col, pb_col = st.columns(2)
        prev_col.metric("📊 Prev", st.session_state.stats_master["prev"])
        pb_col.metric("🏆 PB", st.session_state.stats_master["best"])

        st.markdown('<div class="section-title">Shadow Streaks</div>', unsafe_allow_html=True)
        for pillar in PILLARS.values():
            stat = st.session_state[pillar["stat"]]["current"]
            st.markdown(f"**{pillar['label']}** <span style='float:right'>{stat}</span>", unsafe_allow_html=True)

        st.divider()
        with st.expander("⚛️ Reset App"):
            st.warning("This clears habits, events, and streaks. Saved notes remain untouched.")
            if st.button("⚒️ Reset Forge", use_container_width=True):
                reset_forge()
                st.rerun()

        st.divider()
        if st.button("Simulate Midnight Reset", use_container_width=True):
            process_midnight()
            st.rerun()

        if st.session_state.get("authenticated"):
            st.divider()
            display_name = html.escape(st.session_state.current_display_name)
            st.markdown(
                f"""
                <div class="profile-row">
                    <div class="profile-avatar">🤖</div>
                    <div class="profile-name">{display_name}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button("Logout", use_container_width=True):
                logout_user()


def render_header():
    total = len(st.session_state.habits)
    completed = sum(1 for h in st.session_state.habits if h["done"])
    percent = int((completed / total) * 100) if total else 0

    st.markdown(
        f"""
        <div class="glass-panel hero-panel">
            <h1>StreakForge</h1>
            <p>Track daily execution, deadlines, notes, and streaks from one focused forge.</p>
            <div class="stat-row">
                <div class="stat-card"><small>Daily Progress</small><strong>{completed}/{total}</strong></div>
                <div class="stat-card"><small>Completion</small><strong>{percent}%</strong></div>
                <div class="stat-card"><small>Active Events</small><strong>{len(st.session_state.events)}</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_forge():
    forge_edit_open = can_edit_forge()
    active_index = list(PILLARS).index(st.session_state.active_pillar)
    selected = st.radio(
        "Select Pillar",
        list(PILLARS),
        format_func=lambda key: PILLARS[key]["label"],
        horizontal=True,
        index=active_index,
        label_visibility="collapsed",
    )
    if selected != st.session_state.active_pillar:
        st.session_state.active_pillar = selected
        save_current_user_state()
        st.rerun()

    with st.form("habit_form", clear_on_submit=True):
        col1, col2 = st.columns([0.82, 0.18], vertical_alignment="bottom")
        with col1:
            new_habit = st.text_input(
                "Add Habit",
                placeholder="e.g., Drink 3L of water",
                label_visibility="collapsed",
            )
        with col2:
            submitted = st.form_submit_button("+ Add", use_container_width=True)

        if submitted:
            if not forge_edit_open:
                show_after_10_warning("Forge list")
            elif new_habit.strip():
                st.session_state.habits.append(
                    {
                        "id": len(st.session_state.habits),
                        "text": new_habit.strip(),
                        "pillar": st.session_state.active_pillar,
                        "done": False,
                    }
                )
                save_current_user_state()
                st.rerun()

    st.write("")
    if not st.session_state.habits:
        st.markdown(
            """
            <div class="glass-panel empty-forge">
                <div class="empty-orb">🔨</div>
                <h2>Your Forge is empty.</h2>
                <p>Add a habit above to start forging your discipline.<br>
                <span style="color:#8f86a3;font-style:italic;">Consistent action builds unbreakable streaks.</span></p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    total = len(st.session_state.habits)
    completed = sum(1 for h in st.session_state.habits if h["done"])
    st.progress(completed / total, text=f"Daily Progress: {completed}/{total} Completed")

    for i, habit in enumerate(st.session_state.habits):
        col1, col2, col3 = st.columns([0.72, 0.14, 0.14], vertical_alignment="center")
        with col1:
            checked = st.checkbox(
                f"{pillar_label(habit['pillar'])} | {habit['text']}",
                value=habit["done"],
                key=f"hbt_{i}",
            )
        with col2:
            edit_label = "Close" if st.session_state.get(f"edit_hbt_{i}", False) else "Edit"
            if st.button(edit_label, key=f"edit_btn_hbt_{i}", use_container_width=True):
                if not forge_edit_open:
                    show_after_10_warning("Forge list")
                else:
                    st.session_state[f"edit_hbt_{i}"] = not st.session_state.get(f"edit_hbt_{i}", False)
                    st.rerun()
        with col3:
            if st.button("Delete", key=f"del_hbt_{i}", use_container_width=True):
                if not forge_edit_open:
                    show_after_10_warning("Forge list")
                else:
                    st.session_state.habits.pop(i)
                    for key in list(st.session_state.keys()):
                        if key.startswith("hbt_") or key.startswith("edit_hbt_"):
                            del st.session_state[key]
                    save_current_user_state()
                    st.rerun()

        if checked != habit["done"]:
            st.session_state.habits[i]["done"] = checked
            save_current_user_state()
            st.rerun()

        if st.session_state.get(f"edit_hbt_{i}", False):
            with st.form(f"edit_habit_form_{i}"):
                edit_text = st.text_input("Edit Todo", value=habit["text"], key=f"edit_text_hbt_{i}")
                col_a, col_b = st.columns([0.5, 0.5])
                with col_a:
                    save_edit = st.form_submit_button("Save", use_container_width=True)
                with col_b:
                    cancel_edit = st.form_submit_button("Cancel", use_container_width=True)

                if cancel_edit:
                    st.session_state[f"edit_hbt_{i}"] = False
                    st.rerun()

                if save_edit:
                    if not forge_edit_open:
                        show_after_10_warning("Forge list")
                    elif edit_text.strip():
                        st.session_state.habits[i]["text"] = edit_text.strip()
                        st.session_state[f"edit_hbt_{i}"] = False
                        save_current_user_state()
                        st.rerun()


def render_events():
    event_edit_open = can_edit_forge()

    with st.expander("+ Add New Event", expanded=not st.session_state.events):
        with st.form("event_form", clear_on_submit=True):
            evt_title = st.text_input("Event Name", placeholder="e.g., Submit Assignment")
            col1, col2 = st.columns(2)
            with col1:
                evt_type = st.radio("Type", ["📅 Timelined", "🕰️ Backlog (Timeless)"], horizontal=True)
            with col2:
                evt_date = st.date_input("Deadline", datetime.date.today())

            submitted = st.form_submit_button("Post Event", use_container_width=True)
            if submitted:
                if not event_edit_open:
                    show_after_10_warning("Event Board")
                elif evt_title.strip():
                    st.session_state.events.append(
                        {
                            "id": len(st.session_state.events) + len(st.session_state.history),
                            "text": evt_title.strip(),
                            "deadline": evt_date if "Timelined" in evt_type else None,
                            "done": False,
                            "done_date": None,
                        }
                    )
                    st.toast("Event posted.", icon="📌")
                    save_current_user_state()
                    st.rerun()

    if not st.session_state.events:
        st.info("No active events. Your board is clear.")
        return

    for i, evt in enumerate(st.session_state.events):
        date_str = f"Due: {evt['deadline'].strftime('%b %d, %Y')}" if evt["deadline"] else "Timeless"
        col1, col2, col3 = st.columns([0.72, 0.14, 0.14], vertical_alignment="center")
        with col1:
            is_checked = st.checkbox(
                f"{evt['text']} ({date_str})",
                value=evt["done"],
                key=f"evt_{evt['id']}",
            )
        with col2:
            edit_label = "Close" if st.session_state.get(f"edit_evt_{evt['id']}", False) else "Edit"
            if st.button(edit_label, key=f"edit_btn_evt_{evt['id']}", use_container_width=True):
                if not event_edit_open:
                    show_after_10_warning("Event Board")
                else:
                    st.session_state[f"edit_evt_{evt['id']}"] = not st.session_state.get(f"edit_evt_{evt['id']}", False)
                    st.rerun()
        with col3:
            if st.button("Delete", key=f"del_evt_{evt['id']}", use_container_width=True):
                if not event_edit_open:
                    show_after_10_warning("Event Board")
                else:
                    st.session_state.events.pop(i)
                    if f"evt_{evt['id']}" in st.session_state:
                        del st.session_state[f"evt_{evt['id']}"]
                    clear_event_edit_state()
                    save_current_user_state()
                    st.rerun()

        if is_checked != evt["done"]:
            st.session_state.events[i]["done"] = is_checked
            st.session_state.events[i]["done_date"] = datetime.date.today() if is_checked else None
            save_current_user_state()
            st.rerun()

        if st.session_state.get(f"edit_evt_{evt['id']}", False):
            with st.form(f"edit_event_form_{evt['id']}"):
                edit_title = st.text_input("Edit Event Name", value=evt["text"], key=f"edit_title_evt_{evt['id']}")
                edit_type_index = 0 if evt["deadline"] else 1
                col_a, col_b = st.columns(2)
                with col_a:
                    edit_type = st.radio(
                        "Edit Type",
                        ["📅 Timelined", "🕰️ Backlog (Timeless)"],
                        horizontal=True,
                        index=edit_type_index,
                        key=f"edit_type_evt_{evt['id']}",
                    )
                with col_b:
                    edit_date = st.date_input(
                        "Edit Deadline",
                        evt["deadline"] or datetime.date.today(),
                        key=f"edit_date_evt_{evt['id']}",
                    )

                col_save, col_cancel = st.columns(2)
                with col_save:
                    save_edit = st.form_submit_button("Save", use_container_width=True)
                with col_cancel:
                    cancel_edit = st.form_submit_button("Cancel", use_container_width=True)

                if cancel_edit:
                    st.session_state[f"edit_evt_{evt['id']}"] = False
                    st.rerun()

                if save_edit:
                    if not event_edit_open:
                        show_after_10_warning("Event Board")
                    elif edit_title.strip():
                        st.session_state.events[i]["text"] = edit_title.strip()
                        st.session_state.events[i]["deadline"] = edit_date if "Timelined" in edit_type else None
                        st.session_state[f"edit_evt_{evt['id']}"] = False
                        save_current_user_state()
                        st.rerun()


def render_notes():
    with st.form("notes_form", clear_on_submit=True):
        st.subheader("📓 Add a Note")
        note_title = st.text_input("Title", placeholder="e.g., Project Ideas")
        note_content = st.text_area("Content", placeholder="Write your thoughts here...", height=120)

        if st.form_submit_button("💾 Save Note", use_container_width=True) and (note_title or note_content):
            st.session_state.notes_list.append(
                {
                    "title": note_title,
                    "content": note_content,
                    "date": datetime.datetime.now().strftime("%b %d, %Y - %I:%M %p"),
                }
            )
            st.toast("Note saved successfully!", icon="✅")
            save_current_user_state()
            st.rerun()

    st.divider()
    st.subheader("📂 Saved Notes")

    if not st.session_state.notes_list:
        st.info("No notes saved yet.")
        return

    for i, note in enumerate(reversed(st.session_state.notes_list)):
        real_index = len(st.session_state.notes_list) - 1 - i
        with st.container(border=True):
            col1, col2 = st.columns([0.82, 0.18], vertical_alignment="top")
            with col1:
                if note["title"]:
                    st.markdown(f"**{note['title']}**")
                if note["content"]:
                    st.write(note["content"])
                st.caption(f"⏱️ {note['date']}")
            with col2:
                if st.button("Delete", key=f"del_{real_index}", use_container_width=True):
                    st.session_state.notes_list.pop(real_index)
                    save_current_user_state()
                    st.rerun()

            with st.expander("✏️ Edit Note"):
                edit_t = st.text_input("Edit Title", value=note["title"], key=f"et_{real_index}")
                edit_c = st.text_area("Edit Content", value=note["content"], key=f"ec_{real_index}")
                if st.button("💾 Update", key=f"upd_{real_index}", use_container_width=True):
                    st.session_state.notes_list[real_index]["title"] = edit_t
                    st.session_state.notes_list[real_index]["content"] = edit_c
                    save_current_user_state()
                    st.rerun()


def render_archive():
    if not st.session_state.history:
        st.info("Your archive is currently empty.")
        return

    for evt in reversed(st.session_state.history):
        with st.container(border=True):
            completed_on = evt["done_date"].strftime("%b %d, %Y") if evt["done_date"] else "Unknown"
            st.write(f"✅ **{evt['text']}** | Completed on: *{completed_on}*")


inject_styles()

if not st.session_state.authenticated:
    render_auth_page()
    st.stop()

render_sidebar()
render_header()

tab_forge, tab_events, tab_notes, tab_history = st.tabs(
    ["⚔️ The Forge", "📅 Event Board", "📓 Field Notes", "🗃️ Archive"]
)

with tab_forge:
    render_forge()

with tab_events:
    render_events()

with tab_notes:
    render_notes()

with tab_history:
    render_archive()
