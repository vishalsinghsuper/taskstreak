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
import streamlit.components.v1 as components

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
AUTH_COOKIE_NAME = "streakforge_auth"
AUTH_COOKIE_DAYS = 30
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


def get_auth_secret():
    return (
        get_config_value("AUTH_COOKIE_SECRET")
        or get_config_value("COOKIE_SECRET")
        or get_database_url()
        or "streakforge-local-dev-secret-permanent"
    )


def make_auth_token(username):
    expires_at = int(
        (datetime.datetime.now() + datetime.timedelta(days=AUTH_COOKIE_DAYS)).timestamp()
    )
    payload = f"{username}|{expires_at}"
    signature = hmac.new(
        get_auth_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}|{signature}"


def verify_auth_token(token):
    if not token:
        return None

    try:
        username, expires_at, signature = token.split("|", 2)
        expires_at = int(expires_at)
    except (ValueError, TypeError):
        return None

    if expires_at < int(datetime.datetime.now().timestamp()):
        return None

    payload = f"{username}|{expires_at}"
    expected_signature = hmac.new(
        get_auth_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    return username


# --- ROBUST PURE-JS COOKIE MANAGEMENT ---
def inject_cookie_scripts():
    if "pending_cookie" in st.session_state:
        token = st.session_state.pending_cookie
        js = f"""<script>
            var d = new Date();
            d.setTime(d.getTime() + ({AUTH_COOKIE_DAYS}*24*60*60*1000));
            document.cookie = "{AUTH_COOKIE_NAME}={token}; expires=" + d.toUTCString() + "; path=/; SameSite=Lax";
        </script>"""
        components.html(js, height=0, width=0)
        del st.session_state.pending_cookie

    if "delete_cookie" in st.session_state:
        js = f"""<script>
            document.cookie = "{AUTH_COOKIE_NAME}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
        </script>"""
        components.html(js, height=0, width=0)
        del st.session_state.delete_cookie

def set_auth_cookie(username):
    st.session_state.pending_cookie = make_auth_token(username)

def clear_auth_cookie():
    st.session_state.delete_cookie = True


def restore_login_from_cookie():
    if st.session_state.get("authenticated"):
        return

    # Use Streamlit's native cookie reader
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        token = st.context.cookies.get(AUTH_COOKIE_NAME)
    else:
        return

    username = verify_auth_token(token)
    if not username:
        return

    user_data = get_user(username)
    if not user_data:
        clear_auth_cookie()
        return

    st.session_state.authenticated = True
    st.session_state.current_user = username
    st.session_state.current_display_name = user_data["display_name"]
    reset_app_session()
    apply_user_state(load_user_state(username))


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
    return True


def init_database():
    ensure_database_ready(get_database_url() or str(DB_PATH))


def get_user(username):
    init_database()
    row = db_fetchone(
        "SELECT display_name, email, password, created_at FROM users WHERE username = " + db_placeholder(), 
        (username,)
    )
    if row:
        return {
            "display_name": row[0],
            "email": row[1],
            "password": row[2],
            "created_at": row[3]
        }
    return None

def email_exists(email):
    init_database()
    row = db_fetchone("SELECT 1 FROM users WHERE email = " + db_placeholder(), (email,))
    return bool(row)

def create_user(username, display_name, email, password_hash):
    init_database()
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    sql = f"INSERT INTO users (username, display_name, email, password, created_at) VALUES ({db_placeholder()}, {db_placeholder()}, {db_placeholder()}, {db_placeholder()}, {db_placeholder()})"
    db_execute(sql, (username, display_name, email, password_hash, created_at))
    return {"display_name": display_name, "email": email, "password": password_hash, "created_at": created_at}


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


def login_user(username, user_data, remember_me=True):
    st.session_state.authenticated = True
    st.session_state.current_user = username
    st.session_state.current_display_name = user_data["display_name"]
    if remember_me:
        set_auth_cookie(username)
    else:
        clear_auth_cookie()
    reset_app_session()
    apply_user_state(load_user_state(username))
    st.rerun()


def logout_user():
    clear_auth_cookie()
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


def created_after_edit_cutoff():
    return not can_edit_forge()


def can_change_list_item(item):
    return can_edit_forge() or item.get("created_after_edit_cutoff", False)


def show_after_10_warning(area):
    st.warning(f"{area} items created before 10:00 PM cannot be edited or deleted after 10:00 PM.")


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
            touch-action: manipulation;
            -webkit-tap-highlight-color: transparent;
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

        /* ----- UI HIERARCHY OVERRIDES ----- */
        
        /* Make Task/Event text much more prominent */
        [data-testid="stCheckbox"] label span {
            font-size: 1.18rem !important;
            font-weight: 700 !important;
            color: #ffffff !important;
            letter-spacing: 0.01em;
        }
        
        /* Dim text if the checkbox is marked done */
        [data-testid="stCheckbox"][aria-checked="true"] label span {
            color: #8f86a3 !important;
            text-decoration: line-through;
            font-weight: 500 !important;
        }

        /* Completely subdue Streamlit Tertiary buttons for Edit/Delete icons */
        .stButton > button[kind="tertiary"] {
            color: rgba(255, 255, 255, 0.35) !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            font-size: 1.25rem !important;
            padding: 0.2rem !important;
            min-height: 0 !important;
            height: 2.4rem !important;
            transition: 0.2s ease;
        }
        
        .stButton > button[kind="tertiary"]:hover {
            color: #f97316 !important; /* Subtle flare on hover */
            background: rgba(255, 255, 255, 0.08) !important;
            transform: scale(1.05);
        }

        /* ---------------------------------- */

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
            backdrop-filter: blur(14px);
        }

        /* Tabs and mobile nav styling kept exactly the same for PWA function... */
        [data-testid="stTabs"] div[role="tablist"] {
            gap: 0.85rem;
            padding: 0.75rem;
            margin: 0.7rem 0 1.25rem;
            background: rgba(255, 255, 255, 0.07);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 18px;
            box-shadow: 0 22px 60px rgba(0, 0, 0, 0.22);
            backdrop-filter: blur(14px);
        }

        [data-testid="stTabs"] button[role="tab"] {
            min-height: 3.75rem;
            color: #d8d2e8;
            background: rgba(255, 255, 255, 0.045);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 14px;
            font-size: 1.25rem;
            font-weight: 950;
        }

        [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            color: #fff;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.75), rgba(139, 92, 246, 0.62));
            border: 1px solid rgba(255, 255, 255, 0.3);
        }

        @media (max-width: 900px) {
            [data-testid="stAppViewBlockContainer"] {
                padding: 1rem 1rem 7rem; 
            }
            [data-testid="stTabs"] div[role="tablist"] {
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                z-index: 9999;
                margin: 0;
                padding: 0.6rem 0.5rem 1.6rem;
                border-radius: 24px 24px 0 0;
                background: rgba(20, 10, 35, 0.95);
                border-top: 1px solid rgba(255, 255, 255, 0.15);
                box-shadow: 0 -10px 40px rgba(0, 0, 0, 0.6);
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
            <div style="padding: 2rem;">
                <div style="display:inline-grid;place-items:center;width:4rem;height:4rem;border-radius:18px;background:linear-gradient(135deg, #f97316, #8b5cf6);font-size:2rem;margin-bottom:1.4rem;">🔥</div>
                <h1 style="color:#fff;font-size:3.5rem;line-height:0.96;margin:0;">Enter the Forge.</h1>
                <p style="color:#c9c2d8;font-size:1.03rem;margin-top:1rem;">Build your day like a quest: sharpen your habits, clear your events, bank your notes, and watch discipline turn into streak power.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right_col:
        with st.container(border=True):
            if st.session_state.auth_view == "login":
                st.markdown("<h2 style='color:#fff;margin:0;'>Login</h2><p style='color:#a7a0b8;'>Welcome back. Your forge is warm.</p>", unsafe_allow_html=True)
                with st.form("login_form"):
                    username = st.text_input("Username", placeholder="your username")
                    password = st.text_input("Password", type="password", placeholder="your password")
                    remember_me = st.checkbox("Remember me", value=True)
                    submitted = st.form_submit_button("Login", use_container_width=True)

                    if submitted:
                        normalized_username = username.strip().lower()
                        if not normalized_username or not password:
                            st.warning("Enter your username and password.")
                        else:
                            user_data = get_user(normalized_username)
                            if not user_data:
                                st.error("No account found with that username.")
                            elif not verify_password(password, user_data["password"]):
                                st.error("Incorrect password.")
                            else:
                                login_user(normalized_username, user_data, remember_me)

                if st.button("Create a new account", use_container_width=True):
                    st.session_state.auth_view = "signup"
                    st.rerun()

            else:
                st.markdown("<h2 style='color:#fff;margin:0;'>Create Account</h2><p style='color:#a7a0b8;'>Claim your forge and start stacking wins.</p>", unsafe_allow_html=True)
                with st.form("signup_form"):
                    display_name = st.text_input("Name", placeholder="Vishal")
                    new_username = st.text_input("Choose Username", placeholder="vishal")
                    email = st.text_input("Email", placeholder="you@example.com")
                    new_password = st.text_input("Create Password", type="password", placeholder="At least 6 characters")
                    confirm_password = st.text_input("Confirm Password", type="password")
                    submitted = st.form_submit_button("Create Account", use_container_width=True)

                    if submitted:
                        normalized_username = new_username.strip().lower()
                        clean_email = email.strip().lower()

                        if not display_name.strip() or not normalized_username or not clean_email or not new_password:
                            st.warning("Fill in all signup fields.")
                        elif not re.fullmatch(r"[a-z0-9_]{3,20}", normalized_username):
                            st.error("Username must be 3-20 characters using lowercase letters, numbers, or underscore.")
                        elif get_user(normalized_username):
                            st.error("That username is already taken.")
                        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean_email):
                            st.error("Enter a valid email address.")
                        elif email_exists(clean_email):
                            st.error("An account already exists with that email.")
                        elif len(new_password) < 6:
                            st.error("Password must be at least 6 characters.")
                        elif new_password != confirm_password:
                            st.error("Passwords do not match.")
                        else:
                            user_data = create_user(
                                normalized_username, 
                                display_name.strip(), 
                                clean_email, 
                                hash_password(new_password)
                            )
                            login_user(normalized_username, user_data, True)

                if st.button("Back to login", use_container_width=True):
                    st.session_state.auth_view = "login"
                    st.rerun()


def render_sidebar():
    with st.sidebar:
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:0.8rem;margin-bottom:2rem;">
                <div style="display:grid;width:2.5rem;height:2.5rem;place-items:center;border-radius:14px;background:linear-gradient(135deg, #f97316, #8b5cf6);font-size:1.35rem;">🔥</div>
                <div><h1 style="margin:0;font-size:1.3rem;color:#fff;">StreakForge</h1></div>
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
            st.markdown(f"<div style='color:#fff;font-weight:800;margin-bottom:0.5rem;'>🤖 {display_name}</div>", unsafe_allow_html=True)
            if st.button("Logout", use_container_width=True):
                logout_user()


def render_forge():
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
            new_habit = st.text_input("Add Habit", placeholder="e.g., Drink 3L of water", label_visibility="collapsed")
        with col2:
            submitted = st.form_submit_button("+ Add", use_container_width=True)

        if submitted and new_habit.strip():
            st.session_state.habits.append(
                {
                    "id": len(st.session_state.habits),
                    "text": new_habit.strip(),
                    "pillar": st.session_state.active_pillar,
                    "done": False,
                    "created_after_edit_cutoff": created_after_edit_cutoff(),
                }
            )
            save_current_user_state()
            st.rerun()

    st.write("")
    if not st.session_state.habits:
        st.info("Your Forge is empty. Add a habit to start building discipline.")
        return

    total = len(st.session_state.habits)
    completed = sum(1 for h in st.session_state.habits if h["done"])
    st.progress(completed / total, text=f"Daily Progress: {completed}/{total} Completed")
    st.write("")

    # --- IMPLEMENTED SUBTLE ICONS & HEAVY TEXT HIERARCHY ---
    for i, habit in enumerate(st.session_state.habits):
        habit.setdefault("created_after_edit_cutoff", False)
        item_can_change = can_change_list_item(habit)
        
        # New Column Ratio: Gives 88% width to the text, squeezing the ghost buttons to the right
        col1, col2, col3 = st.columns([0.88, 0.06, 0.06], vertical_alignment="center")
        
        with col1:
            checked = st.checkbox(
                f"{pillar_label(habit['pillar'])} | {habit['text']}",
                value=habit["done"],
                key=f"hbt_{i}",
            )
        with col2:
            edit_toggled = st.session_state.get(f"edit_hbt_{i}", False)
            if st.button("❌" if edit_toggled else "✏️", key=f"edit_btn_hbt_{i}", help="Edit Task", type="tertiary", use_container_width=True):
                if not item_can_change:
                    show_after_10_warning("Forge list")
                else:
                    st.session_state[f"edit_hbt_{i}"] = not edit_toggled
                    st.rerun()
        with col3:
            if st.button("🗑️", key=f"del_hbt_{i}", help="Delete Task", type="tertiary", use_container_width=True):
                if not item_can_change:
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
            with st.container(border=True):
                edit_text = st.text_input("Edit Name", value=habit["text"], key=f"edit_text_hbt_{i}")
                if st.button("Save Changes", key=f"save_edit_{i}", use_container_width=True):
                    if not item_can_change:
                        show_after_10_warning("Forge list")
                    elif edit_text.strip():
                        st.session_state.habits[i]["text"] = edit_text.strip()
                        st.session_state[f"edit_hbt_{i}"] = False
                        save_current_user_state()
                        st.rerun()


def render_events():
    with st.expander("+ Add New Event", expanded=not st.session_state.events):
        with st.form("event_form", clear_on_submit=True):
            evt_title = st.text_input("Event Name", placeholder="e.g., Submit Assignment")
            col1, col2 = st.columns(2)
            with col1:
                evt_type = st.radio("Type", ["📅 Timelined", "🕰️ Backlog (Timeless)"], horizontal=True)
            with col2:
                evt_date = st.date_input("Deadline", datetime.date.today())

            submitted = st.form_submit_button("Post Event", use_container_width=True)
            if submitted and evt_title.strip():
                st.session_state.events.append(
                    {
                        "id": len(st.session_state.events) + len(st.session_state.history),
                        "text": evt_title.strip(),
                        "deadline": evt_date if "Timelined" in evt_type else None,
                        "done": False,
                        "done_date": None,
                        "created_after_edit_cutoff": created_after_edit_cutoff(),
                    }
                )
                save_current_user_state()
                st.rerun()

    if not st.session_state.events:
        st.info("No active events. Your board is clear.")
        return

    st.write("")
    for i, evt in enumerate(st.session_state.events):
        evt.setdefault("created_after_edit_cutoff", False)
        item_can_change = can_change_list_item(evt)
        date_str = f"Due: {evt['deadline'].strftime('%b %d, %Y')}" if evt["deadline"] else "Timeless"
        
        # Consistent minimal button layout for Event Board
        col1, col2, col3 = st.columns([0.88, 0.06, 0.06], vertical_alignment="center")
        with col1:
            is_checked = st.checkbox(
                f"{evt['text']} ({date_str})",
                value=evt["done"],
                key=f"evt_{evt['id']}",
            )
        with col2:
            edit_toggled = st.session_state.get(f"edit_evt_{evt['id']}", False)
            if st.button("❌" if edit_toggled else "✏️", key=f"edit_btn_evt_{evt['id']}", help="Edit Event", type="tertiary", use_container_width=True):
                if not item_can_change:
                    show_after_10_warning("Event Board")
                else:
                    st.session_state[f"edit_evt_{evt['id']}"] = not edit_toggled
                    st.rerun()
        with col3:
            if st.button("🗑️", key=f"del_evt_{evt['id']}", help="Delete Event", type="tertiary", use_container_width=True):
                if not item_can_change:
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
            with st.container(border=True):
                edit_title = st.text_input("Edit Name", value=evt["text"], key=f"edit_title_evt_{evt['id']}")
                if st.button("Save Changes", key=f"save_edit_evt_{evt['id']}", use_container_width=True):
                    if not item_can_change:
                        show_after_10_warning("Event Board")
                    elif edit_title.strip():
                        st.session_state.events[i]["text"] = edit_title.strip()
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
                if st.button("Delete", key=f"del_note_{real_index}", use_container_width=True):
                    st.session_state.notes_list.pop(real_index)
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


# Boot Sequence
inject_styles()
inject_cookie_scripts() # Safely execute any pending browser cookie injections
restore_login_from_cookie()

if not st.session_state.authenticated:
    render_auth_page()
    if not st.session_state.authenticated:
        st.stop()

# Main Application Render
render_sidebar()

st.markdown("<div style='text-align:center;padding:1rem 0 2rem;'><h1 style='color:#fff;margin:0;'>StreakForge</h1><p style='color:#a7a0b8;'>Execute the standard.</p></div>", unsafe_allow_html=True)

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