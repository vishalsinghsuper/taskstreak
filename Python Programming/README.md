# StreakForge

StreakForge is a Streamlit productivity app for tracking daily habits, events,
notes, streaks, and personal execution.

## Features

- Login and sign up with local password hashing
- Daily Forge habit tracker
- Event Board with deadlines and backlog events
- Field Notes
- Streak stats
- Add, edit, and delete controls for Forge and Event Board
- List editing lock after 10:00 PM

## Run Locally

```powershell
pip install -r requirements.txt
streamlit run app.py
```

The app stores local account data in `streakforge_users.json`, which is ignored
by Git because it can contain private user information.
