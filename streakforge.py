import streamlit as st
import datetime

# --- APP CONFIGURATION & NATIVE 3-DOT MENU ---
about_text = """
**About StreakForge**

StreakForge is a gamified productivity engine designed to forge unbreakable discipline. Built on an "All-or-Nothing" accountability system, it forces you to execute your daily requirements while independently tracking your progress across the different pillars of your life. 

**Core Features:**
* **The Master Forge:** A unified habit tracker enforcing daily discipline.
* **Shadow Streaks:** Granular analytics tracking your Iron, Mind, and General progression.
* **The Event Board:** A tactical space for strict deadlines and timeless goals.

Forged by: **Vishal Kumar Singh**
"""

st.set_page_config(
    page_title="StreakForge", 
    page_icon="⚒️", 
    layout="centered",
    menu_items={
        'Get Help': None,
        'Report a bug': "mailto:vishal.singh.cb24@ggits.net?subject=StreakForge%20Bug%20Report",
        'About': about_text
    }
)

# --- INITIALIZE DATABASE (SESSION STATE) ---
def init_state(name, default_val):
    if name not in st.session_state:
        st.session_state[name] = default_val

# Unified Habit List & Sticky Pillar (DEFAULT IS GENERAL)
init_state("habits", [])
init_state("active_pillar", "🏕️ General")

# Shadow Streak Stats
init_state("stats_master", {'current': 0, 'prev': 0, 'best': 0})
init_state("stats_iron", {'current': 0, 'prev': 0, 'best': 0})
init_state("stats_mind", {'current': 0, 'prev': 0, 'best': 0})
init_state("stats_general", {'current': 0, 'prev': 0, 'best': 0})

# Events, Notes & History
init_state("events", [])
init_state("history", [])
init_state("notes_list", []) 

# --- HELPER: EVALUATE STREAK ---
def eval_streak(stat_key, is_successful):
    stats = st.session_state[stat_key]
    if is_successful:
        stats['current'] += 1
        if stats['current'] > stats['best']:
            stats['best'] = stats['current']
    else:
        stats['prev'] = stats['current']
        stats['current'] = 0

# --- CORE LOGIC: THE MIDNIGHT FORGE ---
def process_midnight():
    habits = st.session_state.habits
    
    iron_habits = [h for h in habits if h['pillar'] == '💪 Iron']
    mind_habits = [h for h in habits if h['pillar'] == '🧠 Mind']
    general_habits = [h for h in habits if h['pillar'] == '🏕️ General']
    
    master_done = all(h['done'] for h in habits) if habits else False
    iron_done = all(h['done'] for h in iron_habits) if iron_habits else False
    mind_done = all(h['done'] for h in mind_habits) if mind_habits else False
    general_done = all(h['done'] for h in general_habits) if general_habits else False
    
    if habits: eval_streak("stats_master", master_done)
    if iron_habits: eval_streak("stats_iron", iron_done)
    if mind_habits: eval_streak("stats_mind", mind_done)
    if general_habits: eval_streak("stats_general", general_done)

    # WIPE WIDGET MEMORY (Fixes the stuck checkbox bug)
    for i, h in enumerate(habits):
        h['done'] = False
        if f"hbt_{i}" in st.session_state:
            st.session_state[f"hbt_{i}"] = False

    active_events = []
    for evt in st.session_state.events:
        if evt['done']:
            st.session_state.history.append(evt)
            # Wipe event widget memory as it moves to archive
            if f"evt_{evt['id']}" in st.session_state:
                del st.session_state[f"evt_{evt['id']}"]
        else:
            active_events.append(evt)
            
    st.session_state.events = active_events
    st.toast("Midnight passed. The Forge resets.", icon="🌙")

# --- SIDEBAR: THE ANALYTICS LEDGER & SETTINGS ---
with st.sidebar:
    st.header("📊 The Ledger")
    
    st.subheader("🔥 Master Streak")
    st.metric(label="Current Streak", value=f"{st.session_state.stats_master['current']} Days")
    
    # Visual Polish for Analytics
    c1, c2 = st.columns(2)
    c1.metric("⏪ Prev", st.session_state.stats_master['prev'])
    c2.metric("🏆 PB", st.session_state.stats_master['best'])
    
    st.divider()
    
    st.markdown("### 🏛️ Shadow Streaks")
    st.write(f"💪 Iron: **{st.session_state.stats_iron['current']}** | 🧠 Mind: **{st.session_state.stats_mind['current']}** | 🏕️ Gen: **{st.session_state.stats_general['current']}**")
    
    st.divider()
    
    st.subheader("⚙️ Reset App")
    with st.expander("Reset Forge"):
        st.warning("This will clear all habits, events, and streaks. **Your saved notes will remain untouched.**")
        if st.button("**CONFIRM RESET**"):
            # Selective reset: leave notes_list out!
            st.session_state.habits = []
            st.session_state.events = []
            st.session_state.history = []
            st.session_state.stats_master = {'current': 0, 'prev': 0, 'best': 0}
            st.session_state.stats_iron = {'current': 0, 'prev': 0, 'best': 0}
            st.session_state.stats_mind = {'current': 0, 'prev': 0, 'best': 0}
            st.session_state.stats_general = {'current': 0, 'prev': 0, 'best': 0}
            st.rerun()

    st.divider()
    st.warning("🛠️ **Developer Tool**")
    if st.button("Simulate Midnight Reset", use_container_width=True):
        process_midnight()
        st.rerun()
# ==========================================
# TAB 1: THE FORGE 
# ==========================================
# --- TABS SETUP ---
tab_forge, tab_events, tab_notes, tab_history = st.tabs([
    "⚔️ The Forge", "📅 Event Board", "📓 Field Notes", "📜 Archive"
])
with tab_forge:
    st.radio(
        "Select Pillar:", 
        ["💪 Iron", "🧠 Mind", "🏕️ General"], 
        key="active_pillar", 
        horizontal=True,
        label_visibility="collapsed"
    )
    
    with st.form("habit_form", clear_on_submit=True):
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            new_habit = st.text_input("Add Habit", placeholder="e.g., Drink 3L of water", label_visibility="collapsed")
        with col2:
            submitted = st.form_submit_button("➕ Add", use_container_width=True)
            
        if submitted and new_habit:
            st.session_state.habits.append({
                "id": len(st.session_state.habits), 
                "text": new_habit, 
                "pillar": st.session_state.active_pillar, 
                "done": False
            })

    st.write("") 

    if not st.session_state.habits:
        st.info("Your Forge is empty. Add a habit above.")
    else:
        total = len(st.session_state.habits)
        completed = sum(1 for h in st.session_state.habits if h['done'])
        st.progress(completed / total, text=f"Daily Progress: {completed}/{total} Completed")
        st.write("")
        
        for i, habit in enumerate(st.session_state.habits):
            with st.container(border=True):
                col_chk, col_txt = st.columns([0.05, 0.95])
                with col_chk:
                    checked = st.checkbox("", value=habit['done'], key=f"hbt_{i}")
                    if checked != habit['done']:
                        st.session_state.habits[i]['done'] = checked
                        st.rerun()
                with col_txt:
                    if checked:
                        st.write(f"~~{habit['pillar']} | **{habit['text']}**~~")
                    else:
                        st.write(f"{habit['pillar']} | **{habit['text']}**")

# ==========================================
# TAB 2: EVENT BOARD 
# ==========================================
with tab_events:
    with st.expander("➕ Add New Event"):
        with st.form("event_form", clear_on_submit=True):
            evt_title = st.text_input("Event Name", placeholder="e.g., Submit Assignment")
            col1, col2 = st.columns(2)
            with col1:
                evt_type = st.radio("Type", ["📅 Timelined", "🕰️ Backlog (Timeless)"], horizontal=True)
            with col2:
                evt_date = st.date_input("Deadline", datetime.date.today())
                
            if st.form_submit_button("Post Event") and evt_title:
                st.session_state.events.append({
                    "id": len(st.session_state.events) + len(st.session_state.history),
                    "text": evt_title, 
                    "deadline": evt_date if "Timelined" in evt_type else None, 
                    "done": False, 
                    "done_date": None
                })
                st.toast("Event posted.", icon="📌")

    st.write("")
    if not st.session_state.events:
        st.caption("No active events. Your board is clear.")
    else:
        for i, evt in enumerate(st.session_state.events):
            with st.container(border=True):
                date_str = f"📅 **Due:** {evt['deadline'].strftime('%b %d, %Y')}" if evt['deadline'] else "🕰️ **Timeless**"
                is_checked = st.checkbox(f"{evt['text']} ({date_str})", value=evt['done'], key=f"evt_{evt['id']}")
                
                if is_checked != evt['done']:
                    st.session_state.events[i]['done'] = is_checked
                    st.session_state.events[i]['done_date'] = datetime.date.today() if is_checked else None
                    st.rerun()

# ==========================================
# TAB 3: FIELD NOTES 
# ==========================================
with tab_notes:
    with st.form("notes_form", clear_on_submit=True):
        st.subheader("📓 Add a Note")
        note_title = st.text_input("Title (Optional)", placeholder="e.g., Project Ideas")
        note_content = st.text_area("Content (Optional)", placeholder="Write your thoughts here...", height=100)
        
        if st.form_submit_button("💾 Save Note") and (note_title or note_content):
            st.session_state.notes_list.append({
                "title": note_title,
                "content": note_content,
                "date": datetime.datetime.now().strftime("%b %d, %Y - %I:%M %p")
            })
            st.toast("Note saved successfully!", icon="✅")

    st.divider()
    st.subheader("📂 Saved Notes")
    
    if not st.session_state.notes_list:
        st.caption("No notes saved yet.")
    else:
        for i, note in enumerate(reversed(st.session_state.notes_list)):
            real_index = len(st.session_state.notes_list) - 1 - i
            with st.container(border=True):
                col1, col2 = st.columns([0.85, 0.15])
                with col1:
                    if note["title"]:
                        st.markdown(f"**{note['title']}**")
                    if note["content"]:
                        st.write(note["content"])
                    st.caption(f"⏱️ {note['date']}")
                with col2:
                    if st.button("🗑️ Delete", key=f"del_{real_index}"):
                        st.session_state.notes_list.pop(real_index)
                        st.rerun()
                
                with st.expander("✏️ Edit Note"):
                    edit_t = st.text_input("Edit Title", value=note['title'], key=f"et_{real_index}")
                    edit_c = st.text_area("Edit Content", value=note['content'], key=f"ec_{real_index}")
                    if st.button("💾 Update", key=f"upd_{real_index}"):
                        st.session_state.notes_list[real_index]['title'] = edit_t
                        st.session_state.notes_list[real_index]['content'] = edit_c
                        st.rerun()

# ==========================================
# TAB 4: HISTORY ARCHIVE
# ==========================================
with tab_history:
    if not st.session_state.history:
        st.caption("Your archive is currently empty.")
    else:
        for evt in reversed(st.session_state.history):
            with st.container(border=True):
                st.write(f"✅ **{evt['text']}** | *Completed on: {evt['done_date'].strftime('%b %d, %Y')}*")