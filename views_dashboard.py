"""
views_dashboard.py
==================
MODE 2 - THE SPAR SECURITY COMMAND DASHBOARD (control room).

Features required by the project document, all implemented here:

* Username/password login (``spar_security`` / ``spar2026``).
* Automatic refresh every 3 seconds (Streamlit *fragments* with
  ``run_every=3``), which simulates a real-time WebSocket ingestion feed
  without ever blocking the operator's mouse.
* Interactive map (``streamlit-folium``) showing blue security posts, green
  available officers and a pulsing RED marker for every ACTIVE SOS alert -
  plus a 100 % offline SVG site plan that needs no internet at all.
* Alert queue with "🚓 Dispatch Officer" and "✅ Mark Resolved" actions.
* LOUD automatic siren the moment a new ACTIVE alert appears (base64 WAV
  embedded in a component, with a Web Audio fallback that defeats the
  browser autoplay policy), a flashing red dashboard border and a prominent
  "🔔 Silence Alarm" button.
* Haversine geofencing: every alert card shows the distance in metres from
  the mall centre, and a dedicated panel shows the formula worked out with
  the real numbers for the examiner.
"""

from __future__ import annotations

import math
import random
import time
from html import escape

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

import db
from utils import (
    CATEGORIES,
    GEOFENCE_RADIUS_M,
    SPAR_CENTER_LAT,
    SPAR_CENTER_LON,
    SPAR_NAME,
    WIDTH_STRETCH,
    age_seconds,
    category_meta,
    emergency_border_css,
    eta_seconds,
    fmt_ago,
    fmt_datetime,
    fmt_distance,
    fmt_time,
    haversine_distance_m,
    lan_ip,
    now_wat,
    render_alarm_strip,
    render_site_plan,
    status_meta,
)

REFRESH_SECONDS = 3

TILE_CHOICES = {
    "OpenStreetMap (needs internet)": "OpenStreetMap",
    "CartoDB Positron - light (needs internet)": "CartoDB positron",
    "Esri World Imagery - satellite (needs internet)": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
    ),
}

#: A tiny legend image drawn with pure HTML/CSS (no external assets).
LEGEND_HTML = """
<div class="spar-legend" style="padding:6px 2px 2px 2px;">
  <span>🔵 Security post</span>
  <span>🟢 Officer available</span>
  <span>🟠 Officer dispatched</span>
  <span>🔴 Active SOS alert</span>
  <span>🟣 Pending (cancel window)</span>
</div>
"""


# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
def _init_state() -> None:
    defaults: dict[str, object] = {
        "dash_authed": False,
        "dash_user": None,
        "dash_silenced_ids": [],      # alert ids the operator has acknowledged
        "dash_known_active": [],      # active ids seen on the previous tick
        "dash_alarm_test_until": 0.0, # "test alarm" button countdown
        "dash_map_view": "📍 Offline site plan (no internet needed)",
        "dash_map_center": (SPAR_CENTER_LAT, SPAR_CENTER_LON),
        "dash_map_zoom": 18,
        "dash_tiles": "OpenStreetMap (needs internet)",
        "dash_live_toggle": True,
        "dash_signature": None,
        "dash_flash": {},
        "dash_last_full_refresh": now_wat().isoformat(timespec="seconds"),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _logout() -> None:
    for key in ("dash_authed", "dash_user", "dash_silenced_ids", "dash_known_active",
                "dash_live_toggle", "dash_signature"):
        st.session_state.pop(key, None)
    _init_state()
    st.session_state.dash_authed = False


# ---------------------------------------------------------------------------
# LOGIN SCREEN
# ---------------------------------------------------------------------------
def _render_login() -> None:
    st.markdown(
        """
<div style="text-align:center;padding:10px 0 0 0;">
  <div style="font-size:2.4rem;">🛡️</div>
  <h2 style="margin:4px 0 0 0;color:#111827;">Spar Enugu Security Command</h2>
  <p style="color:#6b7280;font-size:14px;margin:4px 0 0 0;">
    Emergency Alert Monitoring Dashboard &nbsp;|&nbsp; authorised personnel only
  </p>
</div>""",
        unsafe_allow_html=True,
    )

    left, mid, right = st.columns([1, 2, 1])
    with mid:
        with st.container(border=True):
            st.markdown("#### 🔐 Control-room login")

            # ------------------------------------------------------------------
            # WHY THIS BUTTON COMES FIRST:
            # Streamlit raises StreamlitAPIException("...cannot be modified after
            # the widget with key ... is instantiated") when a widget's session
            # state key is written to after that widget has already been created
            # in the same script run.  The button that fills the form therefore
            # has to be rendered BEFORE the username/password fields; the fields
            # then adopt these values as their initial state.
            # ------------------------------------------------------------------
            if st.button(
                "🧪 Fill demo credentials (spar_security)",
                key="dash_prefill",
                **WIDTH_STRETCH,
                help="Puts the demo control-room account into the boxes below so that "
                     "nothing has to be typed during the demonstration.",
            ):
                st.session_state["dash_login_user"] = "spar_security"
                st.session_state["dash_login_pass"] = "spar2026"

            with st.form("dash_login_form"):
                username = st.text_input("Username", key="dash_login_user",
                                         placeholder="spar_security")
                password = st.text_input("Password", key="dash_login_pass",
                                         type="password", placeholder="••••••••")
                submitted = st.form_submit_button("🔓 Login to dashboard",
                                                  **WIDTH_STRETCH, type="primary")

            st.caption(
                "Demo logins → **spar_security / spar2026** (Administrator) · "
                "**control_room / control2026** (Supervisor). "
                "Passwords are stored as salted PBKDF2-SHA256 hashes, never in plain text."
            )

        if submitted:
            user = db.authenticate_dashboard_user(username, password)
            if user:
                st.session_state.dash_authed = True
                st.session_state.dash_user = {
                    "username": user["username"],
                    "full_name": user["full_name"],
                    "role": user["role"],
                }
                st.session_state.dash_silenced_ids = []
                st.session_state.dash_known_active = []
                st.session_state.dash_signature = None
                # render_dashboard() picks this up immediately after this
                # function returns and draws the live dashboard.
            else:
                st.error("❌ Invalid username or password. Access attempt logged by the system.")

        with st.expander("ℹ️ About this dashboard"):
            st.markdown(
                "* Ingests SOS alerts from the citizen mobile application through the shared "
                "local SQLite database (`data/spar_sos.db`).\n"
                "* Auto-refreshes every **3 seconds** to simulate real-time WebSocket "
                "ingestion.\n"
                "* Sounds an audible siren and flashes the screen border whenever a new "
                "**ACTIVE** alert arrives.\n"
                "* Dispatches the **nearest available officer** using the Haversine "
                "great-circle distance.\n"
                "* Records every action in an append-only audit trail."
            )


# ---------------------------------------------------------------------------
# FOLIUM MAP
# ---------------------------------------------------------------------------
_POST_ICON = """
<div style="position:relative;width:34px;height:34px;">
  <div style="position:absolute;left:-17px;top:-17px;width:34px;height:34px;border-radius:50%;
              background:#14509b;border:3px solid #ffffff;box-shadow:0 2px 6px rgba(0,0,0,.45);
              display:flex;align-items:center;justify-content:center;color:#fff;font-size:16px;">🏢</div>
  <div style="position:absolute;left:-40px;top:-46px;width:120px;text-align:center;color:#0b2f61;
              font:700 12px 'Segoe UI',sans-serif;text-shadow:0 0 4px #fff,0 0 4px #fff;">{label}</div>
</div>
"""

_OFFICER_ICON = """
<div style="position:relative;width:30px;height:30px;">
  <div style="position:absolute;left:-15px;top:-15px;width:30px;height:30px;border-radius:50%;
              background:{colour};border:3px solid #ffffff;box-shadow:0 2px 6px rgba(0,0,0,.45);
              display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;">👮</div>
</div>
"""

_ALERT_ICON = """
<style>
@keyframes sparMarkerPulse {{
  0%   {{ transform:scale(.65); opacity:.95; }}
  70%  {{ transform:scale(1.9);  opacity:0; }}
  100% {{ transform:scale(1.9);  opacity:0; }}
}}
.spar-alert-marker {{ position:relative; width:40px; height:40px; }}
.spar-alert-marker .core {{
  position:absolute; left:-20px; top:-20px; width:40px; height:40px; border-radius:50%;
  background:radial-gradient(circle,#ff4d4d,#c00000); border:4px solid #fff;
  box-shadow:0 0 18px rgba(225,29,29,.95); display:flex; align-items:center;
  justify-content:center; color:#fff; font-weight:900; font-size:20px; z-index:5;
}}
.spar-alert-marker .wave {{
  position:absolute; left:-20px; top:-20px; width:40px; height:40px; border-radius:50%;
  background:#e11d1d; animation:sparMarkerPulse 1.5s infinite ease-out; z-index:1;
}}
.spar-alert-marker .tag {{
  position:absolute; left:-95px; top:-64px; width:230px; text-align:center; color:#7f1d1d;
  font:800 13px 'Segoe UI',sans-serif; background:rgba(255,255,255,.86); border-radius:8px;
  border:2px solid #e11d1d; padding:2px 6px;
}}
</style>
<div class="spar-alert-marker">
  <div class="wave"></div><div class="wave" style="animation-delay:.55s"></div>
  <div class="core">!</div>
  <div class="tag">{label}</div>
</div>
"""


def _divicon(html: str) -> folium.DivIcon:
    """A pure-HTML marker: no CDN images, so markers work offline."""
    return folium.DivIcon(html=html, icon_size=(0, 0), icon_anchor=(0, 0),
                          class_name="spar-pin")


def build_map(officers: list[dict], alerts: list[dict], pending: list[dict],
              posts: list[dict], center: tuple[float, float],
              zoom: int) -> folium.Map:
    """Build the interactive Leaflet map with all three marker classes."""
    tile_key = TILE_CHOICES.get(st.session_state.get("dash_tiles", ""), "OpenStreetMap")
    remote_tiles = tile_key.startswith("http")
    fig = folium.Map(
        location=[center[0], center[1]],
        zoom_start=zoom,
        tiles=None if remote_tiles else tile_key,
        control_scale=True,
        prefer_canvas=True,
    )
    if remote_tiles:
        folium.TileLayer(
            tiles=tile_key,
            attr="Esri, Maxar, Earthstar Geographics",
            name="Satellite imagery",
        ).add_to(fig)

    # ---- geofence ----------------------------------------------------------
    folium.Circle(
        location=[SPAR_CENTER_LAT, SPAR_CENTER_LON],
        radius=GEOFENCE_RADIUS_M,
        color="#e11d1d", weight=2, dash_array="8 6",
        fill=True, fill_color="#e11d1d", fill_opacity=0.06,
        tooltip=f"Geofence: {GEOFENCE_RADIUS_M:.0f} m radius around the mall centre",
    ).add_to(fig)

    # ---- mall centre -------------------------------------------------------
    folium.Marker(
        [SPAR_CENTER_LAT, SPAR_CENTER_LON],
        icon=_divicon(
            '<div style="position:relative;"><div style="position:absolute;left:-8px;top:-8px;'
            'width:16px;height:16px;border-radius:50%;background:#111827;border:3px solid #fff;">'
            '</div><div style="position:absolute;left:14px;top:-9px;width:230px;color:#111827;'
            'font:700 12px Segoe UI,sans-serif;text-shadow:0 0 4px #fff;">Spar Enugu centre '
            f'({SPAR_CENTER_LAT}, {SPAR_CENTER_LON})</div></div>'
        ),
        tooltip="Mall geo-reference point (6.4521, 7.5089)",
    ).add_to(fig)

    # ---- 1. BLUE: security posts ------------------------------------------
    post_layer = folium.FeatureGroup(name="🔵 Security posts (4)")
    for post in posts:
        folium.Marker(
            [post["latitude"], post["longitude"]],
            icon=_divicon(_POST_ICON.format(label=escape(str(post["post_name"])))),
            tooltip=f"{post['post_name']} · {fmt_distance(haversine_distance_m(post['latitude'], post['longitude']))} from centre",
        ).add_to(post_layer)
    post_layer.add_to(fig)

    # ---- 2. GREEN: officers, ORANGE when dispatched ------------------------
    officer_layer = folium.FeatureGroup(name="🟢 Security officers (3)")
    for officer in officers:
        busy = str(officer["status"]).upper() != "AVAILABLE"
        officer_layer.add_child(folium.Marker(
            [officer["current_lat"], officer["current_long"]],
            icon=_divicon(_OFFICER_ICON.format(colour="#f59e0b" if busy else "#0b6b3a")),
            tooltip=(f"{officer['name']} · {officer['status']} · "
                     f"{officer.get('post_name') or 'roaming'} · "
                     f"{fmt_distance(haversine_distance_m(officer['current_lat'], officer['current_long']))} from centre"),
            popup=folium.Popup(
                f"<b>{escape(str(officer['name']))}</b><br>"
                f"Status: <b>{escape(str(officer['status']))}</b><br>"
                f"Post: {escape(str(officer.get('post_name') or 'Roaming'))}<br>"
                f"Phone: {escape(str(officer.get('phone') or '-'))}<br>"
                f"Distance from mall centre: "
                f"{fmt_distance(haversine_distance_m(officer['current_lat'], officer['current_long']))}",
                max_width=260,
            ),
        ))
    officer_layer.add_to(fig)

    # ---- 3. PURPLE: alerts inside the 5-second cancel window ---------------
    if pending:
        pending_layer = folium.FeatureGroup(name="🟣 Pending alerts (cancel window)")
        for alert in pending:
            meta = category_meta(str(alert["emergency_type"]))
            pending_layer.add_child(folium.Marker(
                [alert["latitude"], alert["longitude"]],
                icon=_divicon(
                    '<div style="position:relative;width:26px;height:26px;">'
                    '<div style="position:absolute;left:-13px;top:-13px;width:26px;height:26px;'
                    'border-radius:50%;background:#6d28d9;border:3px solid #fff;opacity:.9;'
                    'box-shadow:0 0 10px rgba(109,40,217,.8);"></div></div>'
                ),
                tooltip=f"{meta['emoji']} {meta['label']} · {alert.get('citizen_name')} "
                        f"(cancel window open)",
            ))
        pending_layer.add_to(fig)

    # ---- 4. RED pulsing: ACTIVE SOS alerts --------------------------------
    alert_layer = folium.FeatureGroup(name="🔴 ACTIVE SOS alerts")
    for alert in alerts:
        meta = category_meta(str(alert["emergency_type"]))
        distance = float(alert["distance_from_center_m"])
        alert_layer.add_child(folium.Marker(
            [alert["latitude"], alert["longitude"]],
            icon=_divicon(_ALERT_ICON.format(
                label=f"🆘 SOS · {escape(str(alert.get('citizen_name') or 'Citizen'))} "
                      f"· {fmt_distance(distance)}"
            )),
            tooltip=f"{meta['emoji']} {meta['label']} emergency - "
                    f"{alert.get('citizen_name')} - {fmt_distance(distance)} from mall centre",
            popup=folium.Popup(
                f"<b>{meta['emoji']} {meta['label']} SOS</b><br>"
                f"Citizen: {escape(str(alert.get('citizen_name') or '-'))}<br>"
                f"Phone: {escape(str(alert.get('citizen_phone') or '-'))}<br>"
                f"Status: <b>{escape(str(alert['status']))}</b><br>"
                f"GPS: {alert['latitude']:.6f}, {alert['longitude']:.6f}<br>"
                f"Distance from mall centre: <b>{fmt_distance(distance)}</b><br>"
                f"Raised: {fmt_datetime(alert['created_at'])}",
                max_width=280,
            ),
        ))
    alert_layer.add_to(fig)

    folium.LayerControl(collapsed=True).add_to(fig)
    return fig


# ---------------------------------------------------------------------------
# SMALL UI HELPERS
# ---------------------------------------------------------------------------
def _kpi(label: str, value: str, foot: str, colour: str = "#111827") -> str:
    return f"""
<div class="spar-kpi" style="border-left-color:{colour};">
  <div class="label">{escape(label)}</div>
  <div class="value" style="color:{colour};">{escape(value)}</div>
  <div class="foot">{escape(foot)}</div>
</div>"""


def _alert_card_html(alert: dict) -> str:
    """The alert card shown in the control-room queue."""
    status = str(alert["status"]).upper()
    meta = status_meta(status)
    cat = category_meta(str(alert["emergency_type"]))
    distance = float(alert["distance_from_center_m"])
    inside = distance <= GEOFENCE_RADIUS_M
    citizen = escape(str(alert.get("citizen_name") or "Unregistered citizen"))
    phone = escape(str(alert.get("citizen_phone") or "-"))
    user_type = escape(str(alert.get("citizen_type") or "-"))
    silent = bool(alert.get("citizen_silent_mode"))
    officer = alert.get("officer_name")

    officer_html = (
        f"👮 Dispatched officer: <b>{escape(str(officer))}</b>"
        f" ({escape(str(alert.get('officer_post') or 'post unknown'))})"
        if officer else "👮 No officer assigned yet"
    )
    geofence_html = (
        f'<span class="spar-chip" style="background:rgba(255,255,255,.25);">inside geofence</span>'
        if inside else
        f'<span class="spar-chip" style="background:#7f1d1d;">⚠ OUTSIDE GEOFENCE</span>'
    )
    silent_html = (
        '<span class="spar-chip" style="background:#111827;">🔇 silent mode</span>' if silent else ""
    )
    dispatched = (
        f'<div class="row">🕒 Dispatched: <span class="mono">{fmt_time(alert.get("dispatched_at"))}</span></div>'
        if alert.get("dispatched_at") else ""
    )

    return f"""
<div class="spar-alert-card" style="background:linear-gradient(135deg,{meta['colour']},{cat['dark']});">
  <h4>{meta['emoji']} {cat['emoji']} {cat['label']} — {meta['label']}</h4>
  <div class="row">
    <span class="spar-chip">#SPAR-{int(alert['alert_id']):05d}</span>
    {geofence_html}{silent_html}
  </div>
  <div class="row">👤 Citizen: <b>{citizen}</b> ({user_type}) · 📞 {phone}</div>
  <div class="row">🕒 Raised: <span class="mono">{fmt_time(alert['created_at'])}</span>
      · <span class="mono">{fmt_ago(alert['created_at'])}</span>
      · waiting <span class="mono">{int(age_seconds(alert['created_at']))}s</span></div>
  <div class="row">📍 <b>Distance from Mall Centre: {distance:.1f} metres</b>
      ({fmt_distance(distance)}) — Haversine great-circle distance</div>
  <div class="row">🧭 GPS fix: <span class="mono">{alert['latitude']:.6f}, {alert['longitude']:.6f}</span></div>
  <div class="row">{officer_html}</div>
  {dispatched}
</div>"""


def haversine_working_markdown(alert: dict) -> str:
    """Show the Haversine formula evaluated with the real numbers.

    This satisfies the academic requirement that the geofencing mathematics
    must be *demonstrated*, not merely used.
    """
    lat1, lon1 = float(alert["latitude"]), float(alert["longitude"])
    lat2, lon2 = SPAR_CENTER_LAT, SPAR_CENTER_LON

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    R = 6_371_008.8
    d = R * c

    return f"""
**Alert #SPAR-{int(alert['alert_id']):05d}** — {category_meta(str(alert['emergency_type']))['label']} emergency
raised by **{alert.get('citizen_name') or 'Unregistered citizen'}** at {fmt_datetime(alert['created_at'])}

| Symbol | Meaning | Value |
|---|---|---|
| φ₁, λ₁ | SOS alert position (from the handset GPS) | {lat1:.6f}°, {lon1:.6f}° |
| φ₂, λ₂ | Mall geo-reference point (Spar Enugu) | {lat2:.6f}°, {lon2:.6f}° |
| φ₁ (rad) | latitude of the alert in radians | {phi1:.8f} |
| φ₂ (rad) | latitude of the mall in radians | {phi2:.8f} |
| Δφ | φ₂ − φ₁ (radians) | {dphi:.10f} |
| Δλ | λ₂ − λ₁ (radians) | {dlam:.10f} |
| a | sin²(Δφ/2) + cos φ₁·cos φ₂·sin²(Δλ/2) | {a:.12f} |
| c | 2·atan2(√a, √(1−a)) | {c:.12f} |
| R | mean Earth radius | {R:,.1f} m |
| **d** | **R × c = Haversine distance** | **{d:,.2f} m** |

Geofence decision: **{d:,.2f} m {"≤" if d <= GEOFENCE_RADIUS_M else ">"} {GEOFENCE_RADIUS_M:.0f} m ⇒
{"INSIDE" if d <= GEOFENCE_RADIUS_M else "OUTSIDE"} the mall geofence.**

Nearest officer ETA at 1.35 m/s walking pace: **{eta_seconds(d)} seconds**.

```
a = sin²(Δφ/2) + cos(φ₁)·cos(φ₂)·sin²(Δλ/2)
c = 2·atan2(√a, √(1−a))
d = R·c          (R = {R:,.1f} m)
```
"""


# ---------------------------------------------------------------------------
# THE LIVE PANEL (auto-refreshes every 3 s)
# ---------------------------------------------------------------------------
def _render_side_feed(stats: dict, live: list[dict], pending: list[dict]) -> None:
    """Compact summary rendered into the real Streamlit sidebar."""
    with st.sidebar:
        st.markdown("### 📊 Live feed")
        st.caption(f"Sidebar refreshed at **{fmt_time(now_wat().isoformat(timespec='seconds'))}**")
        st.markdown(
            _kpi("ACTIVE", str(stats["active"]), "awaiting dispatch", "#e11d1d"),
            unsafe_allow_html=True,
        )
        st.markdown(
            _kpi("Pending", str(stats["pending"]), "inside cancel window", "#b8860b"),
            unsafe_allow_html=True,
        )
        st.markdown(
            _kpi("Officers free", f"{stats['officers_available']}/{stats['officers_total']}",
                 "available for dispatch", "#0b6b3a"),
            unsafe_allow_html=True,
        )
        if pending:
            st.warning(f"🟡 {len(pending)} alert(s) inside the 5-second cancel window — "
                       "the citizen may still cancel.")
        st.markdown("---")
        st.markdown("### 📶 Latest alerts")
        if not live:
            st.success("No live alerts.")
        for alert in live[:5]:
            meta = status_meta(str(alert["status"]))
            st.markdown(
                f"{meta['emoji']} **{escape(str(alert.get('citizen_name') or '-'))}** · "
                f"{category_meta(str(alert['emergency_type']))['label']} · "
                f"{fmt_time(alert['created_at'])} · "
                f"**{float(alert['distance_from_center_m']):.0f} m**"
            )
        st.markdown("---")
        st.markdown("### 👮 Officer status")
        for officer in db.officer_position_table(SPAR_CENTER_LAT, SPAR_CENTER_LON):
            busy = str(officer["status"]).upper() != "AVAILABLE"
            st.markdown(
                f"{'🟠' if busy else '🟢'} **{escape(str(officer['name']))}** — "
                f"{escape(str(officer['status']))} · {officer['distance_m']:.0f} m from centre"
            )


def _render_live_body() -> None:
    """Everything that changes in real time lives in this function."""
    # ---- 1. read the shared database -------------------------------------
    posts = db.list_posts()
    officers = db.list_officers()
    active = db.active_alerts()
    live = db.live_alerts()
    pending = db.pending_alerts()
    stats = db.dashboard_stats()

    active_ids = [int(a["alert_id"]) for a in active]
    silenced = set(st.session_state.get("dash_silenced_ids") or [])
    unacknowledged = [a for a in active if int(a["alert_id"]) not in silenced]
    silent_flag = any(bool(a.get("citizen_silent_mode")) for a in unacknowledged)

    # ---- 2. the alarm -----------------------------------------------------
    testing = time.time() < float(st.session_state.get("dash_alarm_test_until") or 0.0)
    ring = bool(unacknowledged) or testing

    if testing:
        detail = "TEST ALARM - the siren you can hear is the live emergency alarm"
    elif unacknowledged:
        first = unacknowledged[0]
        detail = (f"{len(unacknowledged)} ACTIVE emergency alert(s) - latest: "
                  f"{category_meta(str(first['emergency_type']))['label']} from "
                  f"{first.get('citizen_name') or 'a citizen'}")
    else:
        detail = ("All active alerts acknowledged - siren silenced by the operator."
                  if active else "Alarm idle - no unacknowledged active alert.")

    render_alarm_strip(ring, detail, silent_hint=silent_flag)

    # Expose the alarm state for diagnostics/tests: the siren itself lives in a
    # component iframe, so this is how the rest of the program (and the
    # automated test suite) can verify that the alarm really is ringing.
    st.session_state["dash_alarm_ring"] = ring
    st.session_state["dash_alarm_detail"] = detail

    # ---- 3. detect brand new alerts (toast + log) -------------------------
    known = set(st.session_state.get("dash_known_active") or [])
    new_ids = [i for i in active_ids if i not in known]
    if new_ids:
        for alert_id in new_ids:
            alert = db.get_alert(int(alert_id))
            if alert:
                st.toast(
                    f"🚨 NEW SOS: {category_meta(str(alert['emergency_type']))['label']} — "
                    f"{alert.get('citizen_name')} — "
                    f"{float(alert['distance_from_center_m']):.0f} m from centre",
                    icon="🚨",
                )
        st.session_state.dash_known_active = active_ids
    elif not active_ids and known:
        st.session_state.dash_known_active = []

    # ---- 4. emergency banner ---------------------------------------------
    if unacknowledged:
        names = ", ".join(
            f"{category_meta(str(a['emergency_type']))['emoji']} "
            f"{escape(str(a.get('citizen_name') or 'Citizen'))}"
            for a in unacknowledged[:3]
        )
        st.markdown(
            f'<div class="spar-banner">🚨 {len(unacknowledged)} ACTIVE EMERGENCY ALERT(S) '
            f'— IMMEDIATE RESPONSE REQUIRED<br>'
            f'<span style="font-weight:600;font-size:.95rem;">{names}'
            f'{" …" if len(unacknowledged) > 3 else ""} '
            f'&nbsp;|&nbsp; Oldest alert waiting '
            f'{int(age_seconds(unacknowledged[0]["created_at"]))} seconds</span></div>',
            unsafe_allow_html=True,
        )
    elif live:
        st.info("🚓 Officers are attending to dispatched alert(s). No un-acknowledged "
                "emergency is outstanding.")

    # ---- 5. KPI strip -----------------------------------------------------
    kpi_columns = st.columns(5)
    kpis = (
        ("Active alerts", str(stats["active"]), "unacknowledged SOS", "#e11d1d"),
        ("Dispatched", str(stats["dispatched"]), "officers on the way", "#14509b"),
        ("Pending", str(stats["pending"]), "inside cancel window", "#b8860b"),
        ("Resolved today", str(stats["resolved_today"]), "closed by control room", "#0b6b3a"),
        ("Avg response", f"{stats['avg_response_s']:.0f}s", "mean time to resolution", "#6d28d9"),
    )
    for column, (label, value, foot, colour) in zip(kpi_columns, kpis):
        with column:
            st.markdown(_kpi(label, value, foot, colour), unsafe_allow_html=True)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

    # ---- 6. queue (left) + map (right) -----------------------------------
    queue_column, map_column = st.columns([1.05, 2.0], gap="medium")

    with queue_column:
        st.markdown(f"#### 🚨 Alert queue ({len(live)} live)")
        if not live:
            st.markdown(
                '<div class="spar-alert-card" style="background:linear-gradient(135deg,#0b6b3a,#064a27);">'
                "<h4>✅ ALL CLEAR</h4>"
                "<div class='row'>No live alerts. All officers are on patrol across the "
                f"{SPAR_NAME} compound.</div></div>",
                unsafe_allow_html=True,
            )
        for alert in live[:6]:
            alert_id = int(alert["alert_id"])
            status = str(alert["status"]).upper()
            st.markdown(_alert_card_html(alert), unsafe_allow_html=True)

            flash = st.session_state.get("dash_flash", {}).get(alert_id)
            if flash:
                st.success(flash)

            button_columns = st.columns(3)
            if status == "ACTIVE":
                with button_columns[0]:
                    if st.button("🚓 Dispatch Officer", key=f"dispatch_{alert_id}",
                                 **WIDTH_STRETCH, type="primary"):
                        assignment = db.dispatch_alert(alert_id, actor="spar_security")
                        if assignment:
                            st.session_state.setdefault("dash_flash", {})[alert_id] = (
                                f"🚓 {assignment['officer_name']} dispatched — "
                                f"{assignment['distance_m']:.1f} m away, ETA "
                                f"{assignment['eta_seconds']} seconds "
                                f"(phone {assignment.get('officer_phone') or '-'})"
                            )
                        else:
                            st.session_state.setdefault("dash_flash", {})[alert_id] = (
                                "⚠️ No officer could be assigned."
                            )
                        st.rerun(scope="app")
            else:
                with button_columns[0]:
                    st.markdown(
                        f"<div style='font-size:12.5px;color:#374151;padding-top:8px;'>"
                        f"👮 {escape(str(alert.get('officer_name') or 'Officer dispatched'))}</div>",
                        unsafe_allow_html=True,
                    )

            with button_columns[1]:
                if st.button("✅ Mark Resolved", key=f"resolve_{alert_id}",
                             **WIDTH_STRETCH):
                    db.resolve_alert(alert_id, actor="spar_security")
                    st.session_state.get("dash_flash", {}).pop(alert_id, None)
                    st.toast(f"Alert #SPAR-{alert_id:05d} resolved", icon="✅")
                    st.rerun(scope="app")

            with button_columns[2]:
                if st.button("🔕 Silence", key=f"silence_{alert_id}",
                             **WIDTH_STRETCH):
                    silenced = set(st.session_state.get("dash_silenced_ids") or [])
                    silenced.add(alert_id)
                    st.session_state.dash_silenced_ids = sorted(silenced)
                    st.rerun(scope="app")

            with st.expander(f"📐 Haversine working & audit trail — #{alert_id:05d}"):
                st.markdown(haversine_working_markdown(alert))
                events = db.list_alert_events(alert_id)
                if events:
                    st.markdown("**Audit trail**")
                    for event in events:
                        st.markdown(
                            f"- `{fmt_time(event['created_at'])}` **{event['event']}** — "
                            f"{escape(str(event.get('detail') or ''))} "
                            f"_(by {escape(str(event.get('actor') or 'system'))})_"
                        )
            st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        if pending:
            st.markdown("##### 🟡 Inside the 5-second cancel window")
            for alert in pending:
                st.markdown(
                    f"🟡 **{escape(str(alert.get('citizen_name') or 'Citizen'))}** — "
                    f"{category_meta(str(alert['emergency_type']))['label']} — "
                    f"captured {fmt_ago(alert['created_at'])} "
                    f"(not yet broadcast)"
                )

    with map_column:
        st.markdown("#### 🗺️ Live incident map — Spar Hypermarket, Enugu")
        view = st.session_state.get("dash_map_view", "")
        if view.startswith("📍"):
            render_site_plan(posts, officers, active + pending, height_px=660)
            st.caption(
                "Offline site plan: drawn with inline SVG, so it keeps working with "
                "**no internet connection at all**. Markers pulse natively."
            )
        else:
            fig = build_map(
                officers, active, pending, posts,
                st.session_state.get("dash_map_center", (SPAR_CENTER_LAT, SPAR_CENTER_LON)),
                int(st.session_state.get("dash_map_zoom", 18)),
            )
            result = st_folium(
                fig,
                key="spar_map",
                height=560,
                # streamlit-folium takes its own boolean flag (width="stretch"
                # is not understood by the component wrapper).
                use_container_width=True,
                returned_objects=["center", "zoom"],
            )
            if isinstance(result, dict):
                if result.get("center"):
                    st.session_state.dash_map_center = (
                        result["center"]["lat"], result["center"]["lng"]
                    )
                if result.get("zoom"):
                    st.session_state.dash_map_zoom = int(result["zoom"])
            st.markdown(LEGEND_HTML, unsafe_allow_html=True)
            st.caption(
                "Interactive Leaflet map (streamlit-folium). Base tiles are downloaded from "
                "the internet; the marker overlay itself is pure HTML."
            )

    # ---- 7. keep the rest of the page consistent with the live data -------
    signature = (
        tuple(sorted((int(a["alert_id"]), str(a["status"])) for a in live)),
        tuple(sorted((str(o["officer_id"]), str(o["status"]),
                      round(float(o["current_lat"]), 5), round(float(o["current_long"]), 5))
                     for o in officers)),
    )
    if st.session_state.get("dash_signature") != signature:
        st.session_state.dash_signature = signature
        st.session_state.dash_last_full_refresh = now_wat().isoformat(timespec="seconds")
        # The sidebar, the officer panel and the history table are rendered by
        # the parent script.  A single full rerun the instant the data changes
        # keeps every panel consistent - and it happens only when something
        # genuinely changed, so the operator's map view is never disturbed
        # during quiet periods.
        st.rerun(scope="app")


def _render_incident_details() -> None:
    """Officer deployment panel, history table and analytics (below the fold)."""
    posts = db.list_posts()
    officers = db.list_officers()

    st.markdown("---")
    left, right = st.columns([1, 1], gap="medium")

    with left:
        st.markdown("#### 👮 Officer deployment and response times")
        rows = []
        for officer in officers:
            distance = haversine_distance_m(officer["current_lat"], officer["current_long"])
            rows.append({
                "Officer": officer["name"],
                "Status": officer["status"],
                "Assigned post": officer.get("post_name") or "Roaming",
                "Phone": officer.get("phone") or "-",
                "Distance from centre (m)": f"{distance:.1f}",
                "Colour code": "🟢 available" if str(officer["status"]).upper() == "AVAILABLE"
                else "🟠 dispatched",
            })
        st.dataframe(pd.DataFrame(rows), **WIDTH_STRETCH, hide_index=True)

        if st.button("🔄 Simulate patrol movement (move officers a few metres)",
                     key="dash_patrol", **WIDTH_STRETCH):
            for officer in officers:
                if str(officer["status"]).upper() == "AVAILABLE":
                    db.move_officer(
                        int(officer["officer_id"]),
                        round(float(officer["current_lat"]) + random.uniform(-8, 8) / 111_320, 6),
                        round(float(officer["current_long"]) + random.uniform(-8, 8) / 110_616, 6),
                    )
            st.session_state.dash_signature = None   # force a fresh full refresh
            st.rerun()

        st.caption(
            "Officer positions are refreshed from the `security_officers` table and ranked "
            "with the same Haversine kernel used for the geofence."
        )

    with right:
        st.markdown("#### 🗺️ Security posts and geofence check")
        post_rows = []
        for post in posts:
            distance = haversine_distance_m(post["latitude"], post["longitude"])
            post_rows.append({
                "Post": post["post_name"],
                "Latitude": f"{post['latitude']:.4f}",
                "Longitude": f"{post['longitude']:.4f}",
                "Distance from centre (m)": f"{distance:.1f}",
                "Inside 150 m geofence": "✅ Yes" if distance <= GEOFENCE_RADIUS_M else "⚠️ No",
            })
        st.dataframe(pd.DataFrame(post_rows), **WIDTH_STRETCH, hide_index=True)

        st.markdown("#### 📈 Alerts by category (last 12 hours)")
        history = db.recent_alerts(hours=12, limit=200)
        if history:
            frame = pd.DataFrame([
                {"Category": category_meta(str(a["emergency_type"]))["label"],
                 "Count": 1}
                for a in history
            ])
            counts = frame.groupby("Category", as_index=True)["Count"].sum()
            st.bar_chart(counts.rename("Alerts").to_frame(), height=220)
        else:
            st.info("No alerts recorded in the last 12 hours.")


@st.fragment(run_every=REFRESH_SECONDS)
def _live_panel() -> None:
    """Auto-refreshing fragment (default): the 3-second control-room heartbeat."""
    _render_live_body()


@st.fragment
def _static_panel() -> None:
    """Manual-refresh fragment (fallback): acts only on operator interactions."""
    _render_live_body()


# ---------------------------------------------------------------------------
# MAIN DASHBOARD ENTRY POINT
# ---------------------------------------------------------------------------
def render_dashboard() -> None:
    """Render MODE 2: the Spar Security Command Dashboard."""
    _init_state()

    if not st.session_state.dash_authed:
        # The login card is drawn inside a placeholder so that it can be wiped
        # off the screen in this very run the moment the credentials are
        # accepted - the operator never sees the login form sitting above the
        # dashboard, and no extra rerun is needed.
        login_slot = st.empty()
        with login_slot.container():
            _render_login()
        if not st.session_state.dash_authed:
            return
        login_slot.empty()
        st.toast("Login successful - live feed connected", icon="🔓")

    # Defensive housekeeping: never leave an abandoned PENDING alert in the queue.
    db.sweep_stale_pending(grace_seconds=60)

    active = db.active_alerts()
    live = db.live_alerts()
    pending = db.pending_alerts()
    stats = db.dashboard_stats()

    # ---- flashing red border whenever an emergency is open ----------------
    silenced = set(st.session_state.get("dash_silenced_ids") or [])
    unacknowledged = [a for a in active if int(a["alert_id"]) not in silenced]
    st.markdown(emergency_border_css(bool(unacknowledged)), unsafe_allow_html=True)

    # ---- header -----------------------------------------------------------
    user = st.session_state.dash_user or {}
    head_left, head_right = st.columns([2.2, 1.6])

    with head_left:
        st.markdown(
            f"""
<div style="display:flex;align-items:center;gap:12px;">
  <div style="font-size:2.1rem;">🛡️</div>
  <div>
    <h2 style="margin:0;color:#111827;">Spar Enugu Security Command</h2>
    <div style="color:#6b7280;font-size:13.5px;">
      Emergency Alert Monitoring Dashboard &nbsp;·&nbsp; {escape(str(user.get('full_name') or ''))}
      ({escape(str(user.get('role') or ''))}) &nbsp;·&nbsp; live feed every {REFRESH_SECONDS}s
    </div>
  </div>
</div>""",
            unsafe_allow_html=True,
        )

    with head_right:
        clock_a, clock_b = st.columns([1, 1])
        with clock_a:
            st.markdown(
                f"<div style='text-align:right;font:700 15px Segoe UI,sans-serif;"
                f"color:#111827;padding-top:6px;'>🕒 {now_wat().strftime('%H:%M:%S')}<br>"
                f"<span style='font-weight:500;font-size:12px;color:#6b7280;'>"
                f"{now_wat().strftime('%d %b %Y')} WAT</span></div>",
                unsafe_allow_html=True,
            )
        with clock_b:
            if st.button("🔔 Silence Alarm", key="dash_silence_all",
                         **WIDTH_STRETCH, type="primary" if unacknowledged else "secondary"):
                st.session_state.dash_silenced_ids = sorted(
                    set(list(st.session_state.get("dash_silenced_ids") or []) + [int(a["alert_id"]) for a in active])
                )
                st.session_state.dash_alarm_test_until = 0.0
                st.toast("Alarm silenced for the current alert(s). A new alert will ring again.",
                         icon="🔕")
                st.rerun()

    action_a, action_b, action_c, action_d = st.columns([1, 1, 1, 1])
    with action_a:
        if st.button("🔄 Refresh now", key="dash_refresh_now", **WIDTH_STRETCH):
            st.session_state.dash_signature = None
            st.rerun()
    with action_b:
        if st.button("🔊 Test alarm (5 s)", key="dash_test_alarm", **WIDTH_STRETCH,
                     help="Plays the siren on demand - use it to prove the audio works before "
                          "the live demonstration."):
            st.session_state.dash_alarm_test_until = time.time() + 5.0
            st.session_state.dash_live_toggle = True
            st.toast("Test alarm triggered - listen for the siren", icon="🔊")
            st.rerun()
    with action_c:
        toggle_kwargs = {} if "dash_live_toggle" in st.session_state else {"value": True}
        st.toggle(
            "⏱️ Auto-refresh",
            key="dash_live_toggle",
            help=f"Re-reads the database every {REFRESH_SECONDS} seconds "
                 "(simulated real-time ingestion).",
            **toggle_kwargs,
        )
    with action_d:
        if st.button("🚪 Logout", key="dash_logout", **WIDTH_STRETCH):
            _logout()
            st.rerun()

    # ---- sidebar controls --------------------------------------------------
    with st.sidebar:
        st.markdown("### ⚙️ Map and view controls")
        st.session_state.dash_map_view = st.radio(
            "Map view",
            ["📍 Offline site plan (no internet needed)",
             "🗺️ Interactive map (Leaflet, needs internet)"],
            index=0 if str(st.session_state.get("dash_map_view", "")).startswith("📍") else 1,
            key="dash_map_view_radio",
        )
        if str(st.session_state.dash_map_view).startswith("🗺️"):
            st.session_state.dash_tiles = st.selectbox(
                "Base map tiles", list(TILE_CHOICES.keys()),
                index=list(TILE_CHOICES.keys()).index(
                    st.session_state.get("dash_tiles", "OpenStreetMap (needs internet)")
                ),
            )
        st.markdown("---")

    _render_side_feed(stats, live, pending)

    with st.sidebar:
        st.markdown("---")
        st.markdown("### 🔗 Defence-day links")
        from utils import lan_ip
        st.code(
            f"Dashboard (laptop): http://localhost:8501/?mode=dashboard\n"
            f"SOS app   (phone) : http://{lan_ip()}:8501/?mode=sos",
            language="text",
        )
        st.caption("Open the phone link on a handset connected to the same Wi-Fi/hotspot.")
        st.markdown("---")
        st.caption(
            f"Alert waiting time is measured against the West Africa Time clock. "
            f"Last full refresh: {fmt_time(st.session_state.get('dash_last_full_refresh'))}."
        )

    # ---- everything live ---------------------------------------------------
    if st.session_state.get("dash_live_toggle", True):
        _live_panel()
    else:
        st.caption("⏸️ Auto-refresh is OFF — press **🔄 Refresh now** to poll the database.")
        _static_panel()

    # ---- panels that only need to change when the data changes -------------
    _render_incident_details()
