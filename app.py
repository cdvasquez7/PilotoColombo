import os
import time
import sqlite3
from datetime import datetime
from typing import Tuple, Optional

import pandas as pd
import streamlit as st
from PIL import Image, ImageOps

# Your classifier must return: (bool_is_bottle, predictions_list of tuples (id, label, prob))
from model.classifier import is_bottle

# ---------------- CONFIG ----------------
st.set_page_config(page_title="EcoBottle Colombo", page_icon="♻️", layout="centered")
st.markdown(
    """
    <style>
      h1, h2, h3 { text-align: center; }
      .stButton > button { width: 100%; }
      .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        @media (max-width: 768px) {
        .block-container {
            padding-top: 3rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True
)

DATA_DIR   = "data"
IMG_DIR    = "images"
ASSETS_DIR = "assets"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMG_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

DB_PATH          = os.path.join(DATA_DIR, "recycle.db")
IMG_PATH_CAPTURE = os.path.join(IMG_DIR, "step1.jpg")
THANKS_IMG       = os.path.join(ASSETS_DIR, "thanks_earth.png")

AUTO_RESET_SECS  = 20
ADMIN_PASSCODE   = os.getenv("ADMIN_PASSCODE", "teacher123")  # cámbialo en prod

# ---------------- DB LAYER ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Users & points
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            points INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Claimable tickets & monthly control
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            user_id TEXT PRIMARY KEY,
            available INTEGER NOT NULL DEFAULT 0,         -- unredeemed balance
            claimed_month INTEGER NOT NULL DEFAULT 0,     -- claimed this month
            month_key TEXT NOT NULL DEFAULT '',           -- 'YYYY-MM'
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    # Redemption log (teacher uses)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            qty INTEGER NOT NULL,
            admin_note TEXT,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    # Capture history (optional)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            valid INTEGER NOT NULL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()

def ensure_ticket_row(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM tickets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO tickets (user_id, available, claimed_month, month_key) VALUES (?, 0, 0, '')", (user_id,))
        conn.commit()
    conn.close()

def get_user(user_id: str) -> Optional[Tuple[str, int]]:
    if not user_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name, points FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def create_user(user_id: str, name: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (id, name) VALUES (?, ?)", (user_id, name))
    conn.commit()
    conn.close()
    ensure_ticket_row(user_id)

def get_points(user_id: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def add_point(user_id: str, n: int = 1):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE id = ?", (n, user_id))
    conn.commit()
    conn.close()

def push_history(user_id: str, valid: bool):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO history (user_id, valid) VALUES (?, ?)", (user_id, int(valid)))
    conn.commit()
    conn.close()

def get_ticket_info(user_id: str) -> Tuple[int, int, str]:
    """Return (available, claimed_month, month_key)."""
    ensure_ticket_row(user_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT available, claimed_month, month_key FROM tickets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return (0, 0, "")
    return row[0], row[1], row[2]

def set_ticket_info(user_id: str, available: int, claimed_month: int, month_key: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE tickets SET available=?, claimed_month=?, month_key=? WHERE user_id=?",
        (available, claimed_month, month_key, user_id)
    )
    conn.commit()
    conn.close()

def ensure_month_reset(user_id: str):
    """Reset claimed_month if month changed."""
    available, claimed_month, month_key = get_ticket_info(user_id)
    current_key = datetime.now().strftime("%Y-%m")
    if month_key != current_key:
        set_ticket_info(user_id, available, 0, current_key)

def claimable_tickets_now(user_id: str) -> int:
    """How many tickets can be claimed now: min(points//15, 3 - claimed_this_month)."""
    ensure_month_reset(user_id)
    points = get_points(user_id)
    available, claimed_month, _ = get_ticket_info(user_id)
    by_points = points // 15
    by_limit  = max(0, 3 - claimed_month)  # max 3 / month
    return max(0, min(by_points, by_limit))

def claim_one_ticket(user_id: str) -> bool:
    """Try to claim 1 ticket (cost 15 points). Return True if success."""
    ensure_month_reset(user_id)
    if claimable_tickets_now(user_id) <= 0:
        return False
    # deduct 15 points and add 1 ticket + increment claimed_month
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # deduct points
    cur.execute("UPDATE users SET points = points - 15 WHERE id = ? AND points >= 15", (user_id,))
    if cur.rowcount == 0:
        conn.rollback()
        conn.close()
        return False
    # update tickets
    cur.execute("SELECT available, claimed_month, month_key FROM tickets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback()
        conn.close()
        return False
    available, claimed_month, month_key = row
    current_key = datetime.now().strftime("%Y-%m")
    if month_key != current_key:
        claimed_month = 0
        month_key = current_key
    available += 1
    claimed_month += 1
    cur.execute(
        "UPDATE tickets SET available=?, claimed_month=?, month_key=? WHERE user_id=?",
        (available, claimed_month, month_key, user_id)
    )
    conn.commit()
    conn.close()
    return True

def redeem_tickets(user_id: str, qty: int, admin_note: str = "") -> bool:
    """Teacher redeem `qty` tickets from student's available balance."""
    if qty <= 0:
        return False
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT available FROM tickets WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    available = row[0]
    if available < qty:
        conn.close()
        return False
    # deduct available
    cur.execute("UPDATE tickets SET available = available - ? WHERE user_id = ?", (qty, user_id))
    # log redemption
    cur.execute("INSERT INTO redemptions (user_id, qty, admin_note) VALUES (?, ?, ?)", (user_id, qty, admin_note))
    conn.commit()
    conn.close()
    return True

def redemptions_for_user(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT ts, qty, admin_note FROM redemptions WHERE user_id = ? ORDER BY ts DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

# -------------- UTILS --------------
def unique_filename(prefix: str, ext: str = "jpg") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(IMG_DIR, f"{prefix}_{ts}.{ext}")

def go(view: str):
    st.session_state.step = view
    st.rerun()

def reset_to_start(delay_secs: int = 0):
    if delay_secs:
        with st.spinner(f"Returning in {delay_secs} s..."):
            time.sleep(delay_secs)
    st.session_state.update({
        "login_id": "",
        "temp_user_id": "",
        "img_bytes": None,
        "validated": False,
        "award_given": False,
        "step": "start",
    })
    st.rerun()

def is_plastic_bottle_from_predictions(predictions) -> bool:
    """Heuristic: accept 'water_bottle' or any '*bottle*' excluding wine/beer."""
    labels = [lbl.lower() for _, lbl, _ in predictions]
    if any("water_bottle" in lbl for lbl in labels):
        return True
    for lbl in labels:
        if "bottle" in lbl and "wine" not in lbl and "beer" not in lbl:
            return True
    return False

# -------------- STATE --------------
def ss_init():
    defaults = {
        "step": "start",
        "user_id": None,
        "login_id": "",
        "temp_user_id": "",
        "img_bytes": None,
        "validated": False,
        "award_given": False,
        # admin
        "admin_ok": False,
        "admin_id_query": "",
        "admin_note": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

# -------------- INIT --------------
init_db()
ss_init()

# -------------- ROUTER --------------
# Top nav: tiny links for user/admin


if st.session_state.step == "start":
    st.title("♻️ EcoBottle 💧 Colombo")
    st.caption("Simple and friendly prototype")

    st.session_state.login_id = st.text_input("🔐 Enter your ID", value=st.session_state.login_id)

    login_id_clean = (st.session_state.login_id or "").strip()
    # Botones
    disabled_signin = (login_id_clean == "")
    if st.button("➡️ Sign in", use_container_width=True, disabled=disabled_signin):
        # seguridad extra por si llega vacío
        if not login_id_clean:
            st.error("Please enter your ID.")
        else:
            with st.spinner("Checking user..."):
                user = get_user(login_id_clean)
                if user:
                    st.session_state.user_id = login_id_clean
                    ensure_ticket_row(st.session_state.user_id)
                    go("dashboard")
                else:
                    st.session_state.temp_user_id = login_id_clean
                    go("confirm_register")

    if st.button("🧹 Clear", use_container_width=True):
        st.session_state.login_id = ""
        st.rerun()
    
    if st.button("👨‍💼 Admin", use_container_width=True):
        st.session_state.step = "admin_login"
        st.rerun()


elif st.session_state.step == "confirm_register":
    temp_id = (st.session_state.temp_user_id or "").strip()
    if not temp_id:
        st.warning("ID is empty. Please enter your ID again.")
        if st.button("Back", use_container_width=True):
            go("start")
    else:
        st.warning(f"ID **{temp_id}** not found. Create user?")
        if st.button("Yes, create user", use_container_width=True):
            go("register_form")
        if st.button("No, change ID", use_container_width=True):
            go("start")


elif st.session_state.step == "register_form":
    temp_id = (st.session_state.temp_user_id or "").strip()
    if not temp_id:
        st.warning("ID is empty. Please go back and enter your ID.")
        if st.button("↩️ Back", use_container_width=True):
            go("start")
    else:
        st.title("Create user")
        st.info("Fill the form to continue.")
        st.text_input("ID", value=temp_id, disabled=True)
        name = st.text_input("Full name")
        can_register = bool(name.strip())

        if st.button("✅ Register", use_container_width=True, disabled=not can_register):
            with st.spinner("Creating user..."):
                create_user(temp_id, name.strip())
                st.success("User created!")
                st.session_state.user_id = temp_id
                go("dashboard")

        if st.button("↩️ Back", use_container_width=True):
            go("start")


elif st.session_state.step == "dashboard":
    user = get_user(st.session_state.user_id)
    if user:
        name, points = user
        ensure_ticket_row(st.session_state.user_id)
        ensure_month_reset(st.session_state.user_id)
        available, claimed_month, month_key = get_ticket_info(st.session_state.user_id)
        claimable_now = claimable_tickets_now(st.session_state.user_id)

        st.title(f"Hello, {name}")
        st.write(f"Points: **{points}**")
        st.write(f"Tickets available: **{available}**")
        st.caption(f"Tickets claimed this month: **{claimed_month}/3**")
        if points >= 15:
            st.info("You can claim 1 ticket for every 15 points (max 3 per month).")

        if st.button("📷 New bottle", use_container_width=True):
            st.session_state.img_bytes = None
            st.session_state.validated = False
            st.session_state.award_given = False
            go("capture")

        # Claim ticket
        claim_disabled = (claimable_now <= 0)
        label = "🎟️ Claim 1 ticket (-15 points)"
        if claim_disabled:
            label += " — Not available"
        if st.button(label, use_container_width=True, disabled=claim_disabled):
            ok = claim_one_ticket(st.session_state.user_id)
            if ok:
                st.success("1 ticket claimed.")
            else:
                st.error("Cannot claim now.")
            time.sleep(1)
            st.rerun()

        if st.button("🚪 Sign out", use_container_width=True):
            st.session_state.user_id = None
            go("start")

elif st.session_state.step == "capture":
    st.title("Take a photo")
    st.info("Take one clear photo of the plastic bottle.")

    img_file = st.camera_input("Capture")
    if img_file is not None:
        st.session_state.img_bytes = img_file.getvalue()

    if st.session_state.get("img_bytes") and st.button("✅ Validate", use_container_width=True):
        with open(IMG_PATH_CAPTURE, "wb") as f:
            f.write(st.session_state.img_bytes)

        with st.spinner("Analyzing..."):
            _is_bottle_generic, predictions = is_bottle(IMG_PATH_CAPTURE)
            is_plastic = is_plastic_bottle_from_predictions(predictions)

        img = Image.open(IMG_PATH_CAPTURE)
        border = "green" if is_plastic else "red"
        annotated = ImageOps.expand(img, border=12, fill=border)
        ann_path = unique_filename("capture_annot", "jpg")
        annotated.save(ann_path, format="JPEG", quality=92)

        st.session_state.validated = True
        st.image(ann_path, caption="Analyzed photo", use_container_width=True)

        with st.expander("Details"):
            df = pd.DataFrame(predictions, columns=["ID", "Label", "Prob"])
            df["Prob"] = df["Prob"].apply(lambda x: f"{x*100:.2f}%")
            st.table(df[["Label", "Prob"]])

        if is_plastic:
            if not st.session_state.award_given:
                add_point(st.session_state.user_id, 1)
                push_history(st.session_state.user_id, True)
                st.session_state.award_given = True

            pts = get_points(st.session_state.user_id)
            st.success("Thank you for helping the planet! 🌎")
            st.write(f"You earned **+1 point**. Total: **{pts}**")

            if os.path.exists(THANKS_IMG):
                st.image(THANKS_IMG, caption="Keep recycling!", use_container_width=True)
            else:
                st.markdown("🌍♻️ *Keep recycling!*")

            # Back button + auto reset
            if st.button("Finish / Back to start", use_container_width=True):
                reset_to_start()
            st.caption(f"This screen will reset in {AUTO_RESET_SECS} seconds.")
            time.sleep(AUTO_RESET_SECS)
            reset_to_start()
        else:
            push_history(st.session_state.user_id, False)
            st.error("This is not a plastic bottle. Please try again.")
            st.caption("Tip: center the bottle and avoid background clutter.")

    if st.button("↩️ Cancel / Back", use_container_width=True):
        reset_to_start()

# ---------------- ADMIN VIEWS ----------------
elif st.session_state.step == "admin_login":
    st.title("👨‍💼 Admin")
    st.caption("Teachers only")

    code = st.text_input("Passcode", type="password")
    if st.button("Sign in", use_container_width=True):
        if code == ADMIN_PASSCODE:
            st.session_state.admin_ok = True
            st.session_state.step = "admin_panel"
            st.rerun()
        else:
            st.error("Invalid passcode.")

    if st.button("Back to user", use_container_width=True):
        st.session_state.step = "start"
        st.rerun()

elif st.session_state.step == "admin_panel":
    if not st.session_state.get("admin_ok"):
        st.warning("Please sign in as admin.")
        if st.button("Go to admin login", use_container_width=True):
            st.session_state.step = "admin_login"
            st.rerun()
        st.stop()

    st.title("Admin panel")
    st.caption("Find a student and redeem tickets")

    st.session_state.admin_id_query = st.text_input("Student ID", value=st.session_state.admin_id_query)

    if st.button("Lookup", use_container_width=True):
        user = get_user(st.session_state.admin_id_query)
        if not user:
            st.error("Student not found.")
        else:
            name, points = user
            ensure_ticket_row(st.session_state.admin_id_query)
            ensure_month_reset(st.session_state.admin_id_query)
            available, claimed_month, month_key = get_ticket_info(st.session_state.admin_id_query)

            st.success(f"Student: {name}")
            st.write(f"Points: **{points}**")
            st.write(f"Tickets available: **{available}**")
            st.caption(f"Tickets claimed this month: **{claimed_month}/3**")
            st.caption(f"Month key: {month_key or '(none)'}")

            qty = st.number_input("Qty to redeem", min_value=1, max_value=max(1, available), value=1, step=1)
            note = st.text_input("Note (optional)", value=st.session_state.admin_note)

            if st.button("Redeem tickets", use_container_width=True, disabled=(available <= 0)):
                ok = redeem_tickets(st.session_state.admin_id_query, int(qty), note.strip())
                if ok:
                    st.success(f"Redeemed {int(qty)} ticket(s).")
                else:
                    st.error("Cannot redeem (not enough tickets).")
                time.sleep(1)
                st.rerun()

            st.subheader("Redemptions log")
            rows = redemptions_for_user(st.session_state.admin_id_query)
            if rows:
                df = pd.DataFrame(rows, columns=["When", "Qty", "Note"])
                st.table(df)
            else:
                st.info("No redemptions yet.")

    if st.button("Sign out (admin)", use_container_width=True):
        st.session_state.admin_ok = False
        st.session_state.step = "start"
        st.rerun()
