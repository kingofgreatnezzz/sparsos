"""
views_sos.py
============
MODE 1 - THE SHOPPER / STAFF "ONE-TAP SOS" MOBILE APPLICATION.

Strict UX rule enforced by this module
--------------------------------------
A person in distress cannot type, read a form, or navigate a menu.  The
emergency path therefore contains **no text input field and no form at all**:

    * the handset boots with the citizen's profile ALREADY LOADED (exactly as
      an installed app remembers its owner), so `?mode=sos` opens directly on
      the emergency screen - there is no registration wall,
    * ONE massive red SOS button (about half the viewport) that pulses
      continuously, with six optional one-tap event icons (Robbery, Theft,
      Fight/Assault, Medical, Fire, Other) beside it,
    * a 5-second cancel window with a progress bar and a single big CANCEL
      button, so an accidental press never wastes an officer's time,
    * a Silent Mode toggle, because during an armed robbery the victim's
      handset must not make a single sound.

Registration still exists, but it is never in the way: the profile picker and
the registration form live behind a single "Switch citizen" button and are only
reached on purpose.

Screens
-------
1. IDLE       - the one-tap SOS screen.  This is what the URL opens.
2. COUNTDOWN  - the 5-second cancel window (live countdown fragment).
3. SENT       - "Help is on the way", with live dispatch tracking.
4. PROFILE    - profile picker / registration, reached only on demand.
"""

from __future__ import annotations

import math
import random
import time
from html import escape

import streamlit as st
import streamlit.components.v1 as components

import db
from utils import (
    CATEGORIES,
    QUICK_CATEGORIES,
    SPAR_CENTER_LAT,
    SPAR_CENTER_LON,
    SPAR_NAME,
    WIDTH_STRETCH,
    age_seconds,
    category_meta,
    feedback_html,
    fmt_distance,
    fmt_time,
    haversine_distance_m,
    is_inside_geofence,
    offset_gps,
    status_meta,
)

#: Length of the accidental-press cancel window, in seconds.
CANCEL_WINDOW_S = 5.0

#: Simulated GPS zones inside (and just outside) the compound.  Used by the
#: "simulation settings" expander so that the examiner can watch the Haversine
#: distance change on the dashboard.  ``None`` = random fix inside the mall.
SIMULATION_ZONES: dict[str, tuple[float, float] | None] = {
    "Sales floor (random GPS fix)": None,
    "Main entrance (11 m from centre)": (-11.0, 11.0),
    "Car park, north (55 m from centre)": (33.0, 44.0),
    "Cold storage room (120 m from centre)": (-100.0, -67.0),
    "Loading bay (outside geofence, ~172 m)": (-170.0, -25.0),
}


# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
def _init_state() -> None:
    """Create the session-state keys used by the SOS flow (once per session)."""
    defaults: dict[str, object] = {
        "sos_user": None,           # dict row from the users table
        "sos_phase": "IDLE",        # IDLE | COUNTDOWN | SENT | PROFILE
        "sos_category": "GENERAL",  # selected event (optional - has a default)
        "sos_alert_id": None,       # alert_id of the alert in flight
        "sos_fired_at": 0.0,        # time.time() when the big button was tapped
        "sos_fix_lat": SPAR_CENTER_LAT,
        "sos_fix_lon": SPAR_CENTER_LON,
        "sos_fix_label": "Sales floor",
        "sos_location_on": True,
        "sos_silent": False,
        "sos_sim_zone": "Sales floor (random GPS fix)",
        "sos_history": [],          # alert_ids raised in this session
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _select_citizen(user: dict) -> None:
    """Load a citizen profile onto this handset (one tap - nothing typed)."""
    st.session_state.sos_user = user
    st.session_state.sos_silent = bool(user.get("silent_mode"))
    st.session_state.pop("sos_silent_toggle", None)
    st.session_state.sos_phase = "IDLE"
    st.rerun()


def _reset_to_idle() -> None:
    """Return to the one-tap screen, ready for the next emergency."""
    st.session_state.sos_phase = "IDLE"
    st.session_state.sos_alert_id = None
    st.session_state.sos_fired_at = 0.0
    st.session_state.sos_category = "GENERAL"


def _simulated_gps_fix() -> tuple[float, float, str]:
    """Return the simulated GPS fix for this trigger.

    A real handset would call ``navigator.geolocation``; for the defence demo
    the fix is simulated as a point inside the Spar Enugu compound, which is
    exactly what the project document specifies.  Simulating the fix also
    makes the Haversine distance non-zero and therefore demonstrable.
    """
    zone = st.session_state.get("sos_sim_zone", "Sales floor (random GPS fix)")
    offset = SIMULATION_ZONES.get(zone)

    if offset is None:
        # Scatter the fix randomly inside a 90 m disc - a realistic indoor fix.
        radius = 90.0 * math.sqrt(random.random())
        angle = random.uniform(0.0, 2.0 * math.pi)
        east = radius * math.cos(angle)
        north = radius * math.sin(angle)
        label = "Sales floor (simulated GPS fix)"
    else:
        east, north = offset
        label = zone.split(" (")[0]

    lat, lon = offset_gps(SPAR_CENTER_LAT, SPAR_CENTER_LON, east, north)
    return round(lat, 6), round(lon, 6), label


# ---------------------------------------------------------------------------
# SCREEN 4 (on demand only) - PROFILE PICKER
# ---------------------------------------------------------------------------
def _render_profile_screen() -> None:
    """Choose who is holding this handset - ONE TAP, nothing to type.

    This screen is only reached on purpose (the "Switch citizen" button), or on
    the very first run against a completely empty database.  It is never part of
    the emergency path, because the handset boots with a profile already loaded.
    """
    st.markdown(
        """
<div style="text-align:center;padding:6px 0 2px 0;">
  <div style="font-size:2.6rem;line-height:1;">🆘</div>
  <h2 style="margin:6px 0 0 0;color:#111827;">Spar Enugu Emergency Alert</h2>
  <p style="color:#6b7280;font-size:14px;margin:4px 0 0 0;">
    Mobile-Based Emergency Alert System using GPS
  </p>
</div>""",
        unsafe_allow_html=True,
    )

    st.info(
        "This screen only exists for changing handsets or registering a new citizen. "
        "**A person in an emergency never sees a form** - the red SOS button is always "
        "one tap away."
    )

    current = st.session_state.sos_user or {}
    st.caption(f"Currently loaded on this handset: **{escape(str(current.get('name', '-')))}**")

    if st.button("◀ Back to the SOS button", key="sos_back_to_sos",
                 **WIDTH_STRETCH, type="primary"):
        st.session_state.sos_phase = "IDLE"
        st.rerun()

    # ---- ONE TAP: pick an already-registered citizen ----------------------
    users = db.list_users(limit=8)
    if users:
        st.markdown(
            "<p style='font-weight:800;color:#111827;margin-bottom:4px;'>"
            "👤 Who is holding this handset?</p>",
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        for index, user in enumerate(users[:6]):
            with columns[index % 2]:
                if st.button(
                    f"👤 {user['name']}\n({user['user_type']})",
                    key=f"sos_quick_{user['user_id']}",
                    **WIDTH_STRETCH,
                ):
                    _select_citizen(user)
    else:
        st.warning("No citizen is registered yet. Register one below - a single time only.")

    st.divider()
    if st.button(
        "📝 Register a new citizen",
        key="sos_goto_register",
        **WIDTH_STRETCH,
        help="Optional. Registration normally happens long before any emergency - for "
             "example when the shopper installs the app or joins the mall loyalty scheme.",
    ):
        st.session_state.sos_phase = "REGISTER"
        st.rerun()

    st.caption(
        "Privacy: a citizen's name, phone number and GPS position are stored only in the "
        "Spar Enugu security database on this machine. Nothing is sent to the internet."
    )


# ---------------------------------------------------------------------------
# SCREEN 5 (on demand only) - REGISTRATION
# ---------------------------------------------------------------------------
def _render_register_screen() -> None:
    """Register a new citizen.  Deliberately separated from the emergency screen."""
    st.markdown(
        "<h3 style='margin:2px 0 0 0;color:#111827;'>📝 Register a citizen</h3>"
        "<p style='color:#6b7280;font-size:13.5px;margin:2px 0 0 0;'>"
        "This is done once, calmly, long before an emergency - never by a person in danger."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if st.button("◀ Back", key="sos_register_back", **WIDTH_STRETCH):
        st.session_state.sos_phase = "PROFILE"
        st.rerun()

    with st.form("onboarding_form", clear_on_submit=False):
        name = st.text_input("Full name", key="reg_name",
                             placeholder="e.g. Amina Shopper")
        phone = st.text_input("Phone number", key="reg_phone",
                              placeholder="e.g. 0803 456 7890")
        user_type = st.radio("I am a...", ("Shopper", "Staff"), horizontal=True,
                             index=0, key="reg_type")
        st.markdown("**Permissions**")
        location_on = st.toggle(
            "📍 Enable Location (GPS)", value=True, key="reg_location",
            help="Required - security cannot be sent to you without a GPS fix.",
        )
        silent = st.toggle(
            "🔇 Silent Mode", value=False, key="reg_silent",
            help="Mutes all sounds on this handset. Use it in a robbery so that the alert "
                 "is sent quietly and felt as a vibration instead.",
        )
        submitted = st.form_submit_button("✅ Save this citizen", **WIDTH_STRETCH)

    if submitted:
        clean_name = (name or "").strip()
        clean_phone = (phone or "").strip()
        if len(clean_name) < 2:
            st.error("Please enter the citizen's full name.")
        elif len("".join(ch for ch in clean_phone if ch.isdigit())) < 7:
            st.error("Please enter a valid phone number (at least 7 digits).")
        else:
            user_id = db.create_user(clean_name, clean_phone, user_type, silent)
            st.session_state.sos_location_on = bool(location_on)
            st.success(f"{clean_name} has been saved. Loading the profile...")
            _select_citizen(db.get_user(user_id))


# ---------------------------------------------------------------------------
# SHARED UI PIECES
# ---------------------------------------------------------------------------
def _render_status_bar() -> None:
    """Top status bar: citizen, mall, GPS state (as required by the spec)."""
    user = st.session_state.sos_user or {}
    location_on = st.session_state.sos_location_on
    silent = st.session_state.sos_silent

    gps_html = (
        '<span class="dot-ok">● Location Active</span>'
        if location_on
        else '<span class="dot-warn">● Location OFF</span>'
    )
    silent_html = (
        '<span class="dot-warn">🔇 Silent Mode</span>' if silent else '<span class="dot-ok">🔊 Sound On</span>'
    )

    st.markdown(
        f"""
<div class="spar-statusbar">
  <span>👤 <b>{escape(str(user.get('name', 'Citizen')))}</b></span>
  <span class="sep">|</span>
  <span>📍 {SPAR_NAME}</span>
  <span class="sep">|</span>
  {gps_html}
  <span class="sep">|</span>
  {silent_html}
</div>""",
        unsafe_allow_html=True,
    )


def _render_silent_toggle() -> None:
    """Silent Mode toggle - mirrored into the database for the control room."""
    user = st.session_state.sos_user or {}
    current = bool(st.session_state.sos_silent)

    left, right = st.columns([3, 1])
    with left:
        st.caption(
            "🔇 Silent Mode mutes every sound on this handset. The control room is told "
            "that you are signalling quietly."
        )
    with right:
        new_value = st.toggle("Silent Mode", value=current, key="sos_silent_toggle")

    if new_value != current:
        st.session_state.sos_silent = new_value
        if user.get("user_id"):
            db.set_user_silent_mode(int(user["user_id"]), new_value)
        st.rerun()


def _render_category_picker() -> None:
    """Six concrete emergency events, each ONE tap.  None of them is required.

    They are named after real events - Robbery, Theft, Fight - rather than a
    vague heading such as "Security", because a frightened person recognises
    what is happening in front of them far faster than an abstract category.
    """
    selected = st.session_state.get("sos_category", "GENERAL")

    st.markdown(
        "<p style='text-align:center;font-weight:800;color:#111827;font-size:15px;"
        "margin:2px 0 6px 0;'>What is happening? "
        "<span style='font-weight:600;color:#6b7280;'>(optional &mdash; tap one)</span></p>",
        unsafe_allow_html=True,
    )

    for row in (QUICK_CATEGORIES[:3], QUICK_CATEGORIES[3:]):
        columns = st.columns(3)
        for column, key in zip(columns, row):
            meta = CATEGORIES[key]
            with column:
                tapped = st.button(
                    f"{meta['emoji']}\n{meta['label']}",
                    key=f"cat_{key}",
                    **WIDTH_STRETCH,
                    type="primary" if selected == key else "secondary",
                    help=meta["help"],
                )
            if tapped:
                st.session_state.sos_category = key
                st.rerun()

    if selected == "GENERAL":
        st.caption(
            "ℹ️ Nothing tapped yet - that is fine. Pressing SOS alone still sends the "
            "alert, as 🆘 OTHER."
        )
    else:
        meta = category_meta(selected)
        st.markdown(
            f"<div style='text-align:center;font-weight:800;color:{meta['colour']};"
            f"padding:2px 0 0 0;'>{meta['emoji']} {meta['label']} selected &mdash; "
            f"now press SOS</div>",
            unsafe_allow_html=True,
        )


def _render_simulation_settings() -> None:
    """Demo-only controls (hidden by default so the real UX stays one-tap)."""
    with st.expander("🎛️ Simulation settings (examiner / demo only)"):
        st.caption(
            "The handset GPS fix is simulated for this defence demo. Change the zone to "
            "watch the Haversine distance and the geofence decision change on the "
            "Security Command Dashboard."
        )
        zone_keys = list(SIMULATION_ZONES.keys())
        st.selectbox(
            "Simulated GPS zone",
            zone_keys,
            index=zone_keys.index(
                st.session_state.get("sos_sim_zone", "Sales floor (random GPS fix)")
            ),
            key="sos_sim_zone_select",
        )
        st.session_state.sos_sim_zone = st.session_state["sos_sim_zone_select"]

        toggle_kwargs = {} if "sos_location_toggle" in st.session_state else {"value": True}
        st.toggle("📍 Location service enabled", key="sos_location_toggle", **toggle_kwargs)
        st.session_state.sos_location_on = bool(st.session_state["sos_location_toggle"])
        centre_lat, centre_lon = SPAR_CENTER_LAT, SPAR_CENTER_LON
        st.caption(
            f"Mall geo-reference point: **{centre_lat}, {centre_lon}** "
            f"({SPAR_NAME}) · Geofence radius 150 m"
        )


# ---------------------------------------------------------------------------
# SCREEN 2 - IDLE (the one-tap SOS screen)
# ---------------------------------------------------------------------------
def _render_idle_screen() -> None:
    """The emergency screen: status bar, optional event icons, THE RED BUTTON."""
    _render_status_bar()
    _render_silent_toggle()

    if not st.session_state.sos_location_on:
        st.error(
            "📍 **Location service is OFF.** Security cannot be dispatched to an unknown "
            "position. Tap the button below to switch your GPS back on."
        )
        if st.button("📍 Enable location and continue", key="sos_enable_location",
                     **WIDTH_STRETCH, type="primary"):
            st.session_state.sos_location_on = True
            # The expander's toggle is created later in this run, so writing its
            # key here is allowed - and it keeps both controls in step, otherwise
            # the stale toggle would switch the location straight back off.
            st.session_state["sos_location_toggle"] = True
            st.rerun()

    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

    # ---- optional one-tap event icons (above the button, as specified) ----
    _render_category_picker()
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ---------------- THE ONE MASSIVE RED SOS BUTTON ----------------
    tapped = st.button(
        "🆘\nEMERGENCY SOS",
        key="sos_big",
        **WIDTH_STRETCH,
        disabled=not st.session_state.sos_location_on,
        help="Tap once. A 5-second cancel window opens immediately.",
    )

    st.markdown(
        "<p style='text-align:center;color:#6b7280;font-size:13.5px;margin-top:6px;'>"
        "Tap SOS for instant security response."
        "</p>",
        unsafe_allow_html=True,
    )

    if tapped:
        user = st.session_state.sos_user or {}
        lat, lon, label = _simulated_gps_fix()
        alert_id = db.create_alert(
            user_id=int(user.get("user_id") or 0),
            emergency_type=st.session_state.sos_category,
            latitude=lat,
            longitude=lon,
            status="PENDING",
            note=f"Citizen app trigger ({label}); simulated handset GPS fix",
        )
        st.session_state.sos_alert_id = alert_id
        st.session_state.sos_fired_at = time.time()
        st.session_state.sos_fix_lat = lat
        st.session_state.sos_fix_lon = lon
        st.session_state.sos_fix_label = label
        st.session_state.sos_phase = "COUNTDOWN"
        st.rerun()

    _render_simulation_settings()

    if st.button("👤 Not you? Switch citizen (one tap, nothing to type)",
                 key="sos_switch_user", **WIDTH_STRETCH):
        st.session_state.sos_phase = "PROFILE"
        st.rerun()

    with st.expander("ℹ️ What happens when I press the button?"):
        st.markdown(
            "1. Your pre-registered name, phone number and **simulated GPS fix** "
            f"(within 150 m of {SPAR_NAME}) are captured.\n"
            "2. The alert is recorded in the local SQLite database with the status "
            "`PENDING`.\n"
            "3. A **5-second cancel window** opens - press CANCEL if this was a mistake.\n"
            "4. After 5 seconds the alert becomes `ACTIVE`, the Security Command "
            "Dashboard sounds its siren, and the nearest available officer is dispatched.\n"
            "5. Your handset confirms quietly (vibration) or audibly, unless **Silent Mode** "
            "is on."
        )


# ---------------------------------------------------------------------------
# SCREEN 3 - THE 5-SECOND CANCEL WINDOW
# ---------------------------------------------------------------------------
@st.fragment(run_every=0.4)
def _countdown_fragment() -> None:
    """Live countdown. Re-runs every 0.4 s so the bar animates smoothly.

    Because this is a *fragment*, the rest of the page is not re-rendered,
    which keeps the CANCEL button responsive - a blocking ``time.sleep`` loop
    would freeze the very button the citizen needs most.
    """
    alert_id = st.session_state.get("sos_alert_id")
    fired_at = float(st.session_state.get("sos_fired_at") or 0.0)
    elapsed = time.time() - fired_at
    remaining = max(0.0, CANCEL_WINDOW_S - elapsed)

    # Guard: if the cancel window was somehow abandoned, do not broadcast.
    if elapsed > 30.0:
        if alert_id:
            db.cancel_alert(int(alert_id), actor="citizen_app",
                            reason="Cancel window abandoned (browser closed or stalled)")
        _reset_to_idle()
        st.rerun(scope="app")
        return

    if remaining <= 0.0:
        # ---- the window expired: broadcast the alert ----------------------
        if alert_id:
            db.confirm_alert(int(alert_id))
        st.session_state.sos_phase = "SENT"
        st.session_state.sos_sent_at = time.time()
        st.rerun(scope="app")
        return

    category = category_meta(str(st.session_state.get("sos_category", "GENERAL")))
    seconds_shown = int(math.ceil(remaining))

    st.markdown(
        f"""
<div class="spar-countdown-card">
  <div style="font-size:15px;font-weight:700;letter-spacing:.5px;">
    SENDING {category['emoji']} {category['label']} ALERT
  </div>
  <div class="spar-countdown-number">{seconds_shown}</div>
  <div style="font-size:14.5px;opacity:.92;">
    Press CANCEL now if you pressed the button by mistake.
  </div>
</div>""",
        unsafe_allow_html=True,
    )
    st.progress(min(1.0, remaining / CANCEL_WINDOW_S))
    st.caption(
        f"GPS fix captured: {st.session_state.get('sos_fix_lat')}, "
        f"{st.session_state.get('sos_fix_lon')} "
        f"({st.session_state.get('sos_fix_label', 'sales floor')})"
    )

    if st.button("❌  CANCEL - I PRESSED BY MISTAKE", key="sos_cancel",
                 **WIDTH_STRETCH, type="primary"):
        if alert_id:
            db.cancel_alert(int(alert_id), actor="citizen_app")
        _reset_to_idle()
        st.rerun(scope="app")


def _render_countdown_fragment() -> None:
    """Wrapper that keeps the fragment registration explicit."""
    _countdown_fragment()


# ---------------------------------------------------------------------------
# SCREEN 4 - SUCCESS / LIVE DISPATCH TRACKING
# ---------------------------------------------------------------------------
@st.fragment(run_every=3)
def _dispatch_tracker_fragment(alert_id: int) -> None:
    """Poll the citizen's own alert so the phone shows the live response."""
    alert = db.get_alert(int(alert_id))
    if not alert:
        st.warning("This alert is no longer in the database.")
        return

    status = str(alert["status"]).upper()
    meta = status_meta(status)
    officer = alert.get("officer_name")
    dispatched_at = alert.get("dispatched_at")
    resolved_at = alert.get("resolved_at")

    if status == "ACTIVE":
        body = (
            "Your alert has been **received and broadcast** to the Spar Enugu Security "
            "Command Dashboard. The control room siren is sounding. An officer will be "
            "assigned within seconds."
        )
    elif status == "DISPATCHED":
        body = (
            f"**{escape(str(officer or 'A security officer'))}** has been dispatched to your "
            "position and is on the way. Stay where you are if it is safe to do so."
        )
    elif status == "RESOLVED":
        body = "Your emergency has been **marked as resolved** by the control room. Thank you."
    elif status == "CANCELLED":
        body = "This alert was **cancelled** and no officer was dispatched."
    else:
        body = "Your alert is being processed by the control room."

    st.markdown(
        f"""
<div class="spar-alert-card" style="background:linear-gradient(135deg,{meta['colour']},{meta['colour']}dd);">
  <h4>{meta['emoji']} {meta['label']}</h4>
  <div class="row">🎫 Alert reference: <span class="mono">#SPAR-{int(alert['alert_id']):05d}</span></div>
  <div class="row">🕒 Raised at: <span class="mono">{fmt_time(alert['created_at'])}</span>
      &nbsp;(<span class="mono">{int(age_seconds(alert['created_at']))}s ago</span>)</div>
  <div class="row">📍 Distance from mall centre (Haversine):
      <span class="mono">{fmt_distance(alert['distance_from_center_m'])}</span></div>
  <div class="row">👮 Officer: <span class="mono">{escape(str(officer or 'not yet assigned'))}</span></div>
  <div class="row" style="margin-top:8px;">{body}</div>
</div>""",
        unsafe_allow_html=True,
    )

    if dispatched_at:
        st.caption(f"Dispatched at {fmt_time(dispatched_at)} · Control room: Spar Enugu Security")
    if resolved_at:
        st.caption(f"Resolved at {fmt_time(resolved_at)}")

    if status in ("ACTIVE", "DISPATCHED"):
        st.caption("🔄 Live tracking is refreshing automatically every 3 seconds.")


def _render_success_screen() -> None:
    alert_id = st.session_state.get("sos_alert_id")
    category = category_meta(str(st.session_state.get("sos_category", "GENERAL")))
    distance = haversine_distance_m(
        float(st.session_state.get("sos_fix_lat", SPAR_CENTER_LAT)),
        float(st.session_state.get("sos_fix_lon", SPAR_CENTER_LON)),
    )
    inside = is_inside_geofence(
        float(st.session_state.get("sos_fix_lat", SPAR_CENTER_LAT)),
        float(st.session_state.get("sos_fix_lon", SPAR_CENTER_LON)),
    )

    st.markdown(
        f"""
<div style="text-align:center;padding:18px 10px;border-radius:20px;
            background:linear-gradient(160deg,#0b6b3a,#064a27);color:#fff;
            box-shadow:0 14px 34px rgba(6,74,39,.35);">
  <div style="font-size:3.4rem;line-height:1;">✅</div>
  <h2 style="margin:8px 0 4px 0;color:#fff;">HELP IS ON THE WAY</h2>
  <p style="font-size:16px;margin:0;opacity:.96;">Security has been alerted.</p>
  <p style="font-size:13.5px;margin:8px 0 0 0;opacity:.9;">
    {category['emoji']} {category['label']} emergency &nbsp;·&nbsp; {SPAR_NAME}
  </p>
</div>""",
        unsafe_allow_html=True,
    )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Distance (Haversine)", fmt_distance(distance))
    col_b.metric("Geofence", "Inside" if inside else "Outside")
    col_c.metric("Cancel window", "Passed")

    if not inside:
        st.warning(
            f"⚠️ Your GPS fix is **{fmt_distance(distance)}** from the mall centre, which is "
            "outside the 150 m geofence. The control room has been told, and the nearest "
            "officer will still be dispatched."
        )

    if st.session_state.get("sos_silent"):
        st.info(
            "🔇 **Silent Mode was ON** - your handset made no sound. Confirmation was sent "
            "as a vibration only, so the alert could not be heard by anyone near you. "
            "The control room can see that you signalled quietly."
        )
    else:
        st.caption("🔊 A confirmation tone and vibration were played on your handset.")

    # Audible / haptic feedback (muted entirely by Silent Mode).
    silent = bool(st.session_state.get("sos_silent"))
    components.html(
        feedback_html(
            vibrate=True,
            beep=not silent,
            message=(
                "Silent Mode: vibration only"
                if silent
                else "Confirmation tone played"
            ),
        ),
        height=24,
    )

    if alert_id:
        _dispatch_tracker_fragment(int(alert_id))

    st.divider()
    left, right = st.columns(2)
    with left:
        if st.button("🆘 Send another alert", key="sos_again",
                     **WIDTH_STRETCH, type="primary"):
            _reset_to_idle()
            st.rerun()
    with right:
        if st.button("🏠 Back to home screen", key="sos_home", **WIDTH_STRETCH):
            _reset_to_idle()
            st.rerun()

    st.caption(
        "Keep this screen open. Do not switch the handset off - the control room can see "
        "your position for as long as the alert is open."
    )


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def render_sos_app() -> None:
    """Render MODE 1: the citizen one-tap SOS application.

    Opening ``?mode=sos`` goes STRAIGHT to the emergency screen: the handset
    loads the pre-registered citizen automatically, so a person in danger is
    never asked to register, log in, or type anything.  The profile screen is
    only used when the database is completely empty (one-time setup).
    """
    _init_state()

    # ---- load the citizen profile that belongs to this handset ------------
    if st.session_state.sos_user is None:
        citizen = db.default_citizen()
        if citizen is None:
            # Empty database: somebody has to register once. Never in an emergency.
            _render_profile_screen()
            return
        st.session_state.sos_user = citizen
        st.session_state.sos_silent = bool(citizen.get("silent_mode"))

    if not st.session_state.sos_history and st.session_state.get("sos_alert_id"):
        st.session_state.sos_history = [st.session_state.sos_alert_id]

    phase = st.session_state.sos_phase

    if phase == "PROFILE":
        _render_profile_screen()
        return

    if phase == "REGISTER":
        _render_register_screen()
        return

    if phase == "IDLE":
        _render_idle_screen()
        phase = st.session_state.sos_phase  # the big button may have changed it

    if phase == "COUNTDOWN":
        st.markdown(
            "<h3 style='text-align:center;color:#b91c1c;margin:4px 0 10px 0;'>"
            "🚨 ALERT READY TO SEND</h3>",
            unsafe_allow_html=True,
        )
        _render_countdown_fragment()
    elif phase == "SENT":
        _render_success_screen()
    elif phase not in ("IDLE",):
        _reset_to_idle()
        st.rerun()
