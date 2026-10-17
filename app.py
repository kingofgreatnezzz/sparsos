"""
app.py
======
MAIN ENTRY POINT - "Design and Implementation of a Mobile-Based Emergency Alert
System using GPS (A Case Study of Spar Hypermarket, Enugu State)".

ONE code base, ONE SQLite database, TWO simultaneous views
---------------------------------------------------------
The same Streamlit process serves both modes; the URL query string selects
which one is drawn, so the operator can open two browser tabs and demonstrate
the complete end-to-end user journey live:

    Mode 1 - Citizen handset (open on the phone)      ?mode=sos
    Mode 2 - Security Command Dashboard (laptop)      ?mode=dashboard

    streamlit run app.py
    http://<laptop-ip>:8501/?mode=sos          <- phone
    http://localhost:8501/?mode=dashboard      <- laptop

A sidebar radio also switches modes without editing the URL, which is handy
when there is only one screen available during the defence.
"""

from __future__ import annotations

import streamlit as st

import db
from utils import SPAR_NAME, inject_global_css, lan_ip, now_wat
from views_dashboard import render_dashboard
from views_sos import render_sos_app

MODE_SOS = "sos"
MODE_DASHBOARD = "dashboard"
MODE_ORDER = (MODE_DASHBOARD, MODE_SOS)
MODE_LABELS = {
    MODE_DASHBOARD: "🛡️  Spar Security Command Dashboard (laptop)",
    MODE_SOS: "🆘  Shopper / Staff SOS App (handset)",
}

#: Friendly aliases so the URL can be typed quickly on a phone.
MODE_ALIASES = {
    "sos": MODE_SOS, "citizen": MODE_SOS, "shopper": MODE_SOS,
    "phone": MODE_SOS, "app": MODE_SOS, "emergency": MODE_SOS,
    "dashboard": MODE_DASHBOARD, "dash": MODE_DASHBOARD,
    "security": MODE_DASHBOARD, "admin": MODE_DASHBOARD, "control": MODE_DASHBOARD,
}


def _mode_from_url() -> str | None:
    """Read ``?mode=`` from the URL (tolerates lists and unknown values)."""
    value = st.query_params.get("mode")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    return MODE_ALIASES.get(str(value).strip().lower())


def _ensure_database() -> None:
    """Create the schema when missing and offer to seed an empty database."""
    if not st.session_state.get("_db_ready"):
        db.init_db()
        db.sweep_stale_pending(grace_seconds=120)
        st.session_state["_db_ready"] = True

    health = db.health_check()
    unseeded = (health.get("users", 0) == 0) or (health.get("security_posts", 0) == 0)
    if unseeded:
        st.error(
            "⚠️ **The database has not been seeded yet.** Run `python setup_db.py` in the "
            "project folder, or create the demo data right now with the button below."
        )
        if st.button("🌱 Create and seed the demo database now", key="app_seed_now",
                     type="primary"):
            db.seed_reference_data()
            st.success("Demo data created: citizens, security posts, officers and logins.")
            st.rerun()


def main() -> None:
    # ------------------------------------------------------------------
    # 1. Work out the mode BEFORE any element is created, because the page
    #    layout (wide dashboard vs. narrow handset view) depends on it.
    # ------------------------------------------------------------------
    # A deep link (?mode=sos) is applied ONCE, when the session starts.
    # After that the sidebar switcher owns the mode.
    #
    # This used to re-apply the URL mode on EVERY run, comparing it with the
    # radio's current value.  So the moment the address bar held
    # ?mode=dashboard, a click on "Shopper / Staff SOS App" was overruled on
    # the very next run and the view snapped straight back to the dashboard -
    # the switcher looked completely dead.  Reading the URL only at session
    # start removes that fight for good.
    url_mode = _mode_from_url()
    if "_url_mode_applied" not in st.session_state:
        st.session_state["_url_mode_applied"] = True
        if "mode_radio" not in st.session_state and url_mode:
            st.session_state.mode_radio = url_mode

    if "mode_radio" not in st.session_state:
        st.session_state.mode_radio = MODE_DASHBOARD

    mode = st.session_state.mode_radio

    st.set_page_config(
        page_title=(
            "Spar Security Command Dashboard"
            if mode == MODE_DASHBOARD
            else "Spar Enugu Emergency SOS"
        ),
        page_icon="🛡️" if mode == MODE_DASHBOARD else "🆘",
        layout="wide" if mode == MODE_DASHBOARD else "centered",
        initial_sidebar_state="expanded" if mode == MODE_DASHBOARD else "collapsed",
    )

    inject_global_css()
    _ensure_database()

    # ------------------------------------------------------------------
    # 2. Sidebar: the mode switcher and the demo briefing.
    # ------------------------------------------------------------------
    with st.sidebar:
        st.markdown("### 🎛️ Demo mode")
        st.radio(
            "Choose the view",
            MODE_ORDER,
            key="mode_radio",
            format_func=lambda key: MODE_LABELS[key],
            label_visibility="collapsed",
        )
        st.caption(
            "Both views run from this same program and share "
            "`data/spar_sos.db` in real time. Open them in two tabs."
        )
        st.markdown("---")
        st.markdown("### 🔗 Defence-day URLs")
        st.code(
            f"Shopper SOS app (phone)      : http://{lan_ip()}:8501/?mode=sos\n"
            f"Security dashboard (laptop)  : http://localhost:8501/?mode=dashboard",
            language="text",
        )
        st.caption(
            "The handset must be on the same Wi-Fi network / hotspot as this laptop, and "
            "Windows Firewall must allow Python on the private network."
        )
        st.markdown("---")
        st.markdown("### ℹ️ About")
        st.markdown(
            f"**{SPAR_NAME}**  \n"
            "Mobile-Based Emergency Alert System using GPS  \n"
            "Final Year Computer Science Project  \n"
            f"Local time (WAT): {now_wat().strftime('%d %b %Y, %H:%M')}  \n"
            "100 % local: SQLite + Streamlit, no cloud, no paid API."
        )

    # ------------------------------------------------------------------
    # 3. Render the selected mode.
    # ------------------------------------------------------------------
    mode = st.session_state.mode_radio

    # Keep the address bar in step with the chosen view so the URL stays
    # shareable (e.g. ?mode=sos for the phone).  This only WRITES the URL - it
    # is never fed back into the mode decision above, so the sidebar always
    # wins and the view can no longer snap back.
    try:
        if str(st.query_params.get("mode") or "") != mode:
            st.query_params["mode"] = mode
    except Exception:  # query params are a convenience, never critical
        pass

    if mode == MODE_SOS:
        render_sos_app()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
