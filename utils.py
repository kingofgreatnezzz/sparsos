"""
utils.py
========
Shared constants, geospatial mathematics and user-interface helpers for the
project:

    "Design and Implementation of a Mobile-Based Emergency Alert System
     using GPS - A Case Study of Spar Hypermarket, Enugu State"

This module contains NO database code (see ``db.py``) so that the geospatial
mathematics demanded by the project document - the **Haversine formula** - can
be unit-tested in complete isolation (see ``tests/test_haversine.py``).

Author : Final Year Computer Science Project
Python : 3.10+
"""

from __future__ import annotations

import base64
import math
import socket
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------------------
# 1. PROJECT PATHS
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = BASE_DIR / "assets"
TESTS_DIR = BASE_DIR / "tests"

DB_PATH = DATA_DIR / "spar_sos.db"
ALARM_WAV = STATIC_DIR / "alarm.wav"
ALARM_WAV_COPY = ASSETS_DIR / "alarm.wav"

for _folder in (DATA_DIR, STATIC_DIR, ASSETS_DIR):
    _folder.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 2. GEOGRAPHIC CONSTANTS  (Spar Hypermarket, Enugu State, Nigeria)
# ---------------------------------------------------------------------------
#: Latitude of the geo-reference point of the mall (the "centre").
SPAR_CENTER_LAT: float = 6.4521
#: Longitude of the geo-reference point of the mall.
SPAR_CENTER_LON: float = 7.5089
#: Human readable name used across the interface.
SPAR_NAME: str = "Spar Hypermarket, Enugu"
SPAR_ADDRESS: str = "Polo Park Mall, Abakaliki Road, Enugu State, Nigeria"

#: Mean Earth radius in metres (IUGG mean radius, used by the Haversine formula).
EARTH_RADIUS_M: float = 6_371_008.8

#: Geofence radius of the mall compound in metres (academic requirement:
#: an alert further than this distance is flagged as "outside geofence").
GEOFENCE_RADIUS_M: float = 150.0

#: Average walking speed of a security officer inside the mall, in metres/second.
#: Used to convert a Haversine distance into an estimated response time (ETA).
OFFICER_WALK_SPEED_MPS: float = 1.35

#: West Africa Time = UTC+1 all year round (Nigeria does not observe DST).
WAT = timezone(timedelta(hours=1), "WAT")


# ---------------------------------------------------------------------------
# 3. THE HAVERSINE FORMULA  (ACADEMIC CORE OF THE PROJECT)
# ---------------------------------------------------------------------------
def haversine_distance_m(
    lat1: float,
    lon1: float,
    lat2: float = SPAR_CENTER_LAT,
    lon2: float = SPAR_CENTER_LON,
) -> float:
    """Return the great-circle distance between two GPS points, in METRES.

    The Haversine formula (also called the "great-circle distance" or the
    "law of haversines") gives the shortest distance between two points on the
    surface of a sphere, from their latitudes and longitudes::

        a = sin^2(dphi/2) + cos(phi1) * cos(phi2) * sin^2(dlambda/2)
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        d = R * c

    where

        phi1, phi2 = latitude of point 1 and point 2  (radians)
        dphi       = phi2 - phi1                      (radians)
        dlambda    = lambda2 - lambda1                (radians)
        R          = mean radius of the Earth         (6 371 008.8 m)
        d          = great-circle distance            (metres)

    ``math.atan2`` is used instead of ``math.asin`` because it is numerically
    stable for the very short distances (10 m - 500 m) that occur inside a
    hypermarket compound.

    Parameters
    ----------
    lat1, lon1 : float
        Latitude and longitude of the SOS alert (decimal degrees).
    lat2, lon2 : float
        Latitude and longitude of the reference point; defaults to the centre
        of Spar Hypermarket, Enugu.

    Returns
    -------
    float
        Distance in metres (always >= 0).

    Examples
    --------
    >>> round(haversine_distance_m(6.4521, 7.5089), 3)   # same point
    0.0
    >>> round(haversine_distance_m(6.4522, 7.5088), 1)   # Main Entrance post
    15.7
    """
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    # Guard against tiny floating-point overshoot above 1.0.
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return EARTH_RADIUS_M * c


def metres_per_degree_lat() -> float:
    """Return the length of one degree of latitude in metres at the mall."""
    return EARTH_RADIUS_M * math.pi / 180.0


def metres_per_degree_lon(lat: float = SPAR_CENTER_LAT) -> float:
    """Return the length of one degree of longitude in metres at ``lat``."""
    return EARTH_RADIUS_M * math.pi / 180.0 * math.cos(math.radians(lat))


def offset_gps(
    lat: float, lon: float, east_m: float = 0.0, north_m: float = 0.0
) -> tuple[float, float]:
    """Offset a GPS coordinate by a local metric displacement.

    Used to convert the metric site-plan drawings (metres) into GPS
    coordinates (degrees) that Leaflet understands.
    """
    new_lat = lat + north_m / metres_per_degree_lat()
    new_lon = lon + east_m / metres_per_degree_lon(lat)
    return new_lat, new_lon


def is_inside_geofence(
    lat: float, lon: float, radius_m: float = GEOFENCE_RADIUS_M
) -> bool:
    """Return True when a GPS point lies inside the mall geofence."""
    return haversine_distance_m(lat, lon) <= radius_m


def eta_seconds(distance_m: float, speed_mps: float = OFFICER_WALK_SPEED_MPS) -> int:
    """Estimated response time (seconds) for an officer to reach an alert."""
    if distance_m <= 0:
        return 0
    return int(math.ceil(distance_m / max(speed_mps, 0.1)))


# ---------------------------------------------------------------------------
# 4. EMERGENCY CATEGORIES AND STATUS METADATA
# ---------------------------------------------------------------------------
#: The emergency types selectable with ONE TAP on the citizen screen.
#:
#: They are deliberately named after *real events* ("Robbery", "Theft",
#: "Fight") rather than vague headings like "Security", because a distressed
#: person chooses far faster when the label matches what is happening in front
#: of them.  Choosing one is ALWAYS optional: if nothing is tapped the alert is
#: broadcast as OTHER (GENERAL), so the red button alone is a complete report.
CATEGORIES: dict[str, dict[str, str]] = {
    "ROBBERY": {
        "emoji": "🔫",
        "label": "ROBBERY",
        "colour": "#b91c1c",
        "dark": "#7f1d1d",
        "help": "Armed robbery in progress. Do not confront anybody - alert security quietly.",
    },
    "THEFT": {
        "emoji": "🥷",
        "label": "THEFT",
        "colour": "#a16207",
        "dark": "#713f12",
        "help": "Stolen bag, snatched phone, pickpocket or shoplifting in progress.",
    },
    "ASSAULT": {
        "emoji": "🥊",
        "label": "FIGHT / ASSAULT",
        "colour": "#c2410c",
        "dark": "#7c2d12",
        "help": "Fighting, assault, harassment or a violent customer.",
    },
    "MEDICAL": {
        "emoji": "🏥",
        "label": "MEDICAL",
        "colour": "#0b6b3a",
        "dark": "#064a27",
        "help": "Collapse, injury, snake bite, childbirth, poisoning, difficulty breathing.",
    },
    "FIRE": {
        "emoji": "🔥",
        "label": "FIRE",
        "colour": "#b3541e",
        "dark": "#7d370f",
        "help": "Fire outbreak, smoke, gas leak, electrical sparking.",
    },
    "GENERAL": {
        "emoji": "🆘",
        "label": "OTHER",
        "colour": "#6d28d9",
        "dark": "#4c1d95",
        "help": "Any other emergency - and the automatic default when no icon is tapped.",
    },
}

#: The event icons offered on the citizen screen, in display order (two rows of
#: three).  Every one of them is a single tap, and none of them is required.
QUICK_CATEGORIES: tuple[str, ...] = (
    "ROBBERY", "THEFT", "ASSAULT",
    "MEDICAL", "FIRE", "GENERAL",
)

#: Older rows in the database (and the original project wording) used the
#: broader heading "SECURITY".  These aliases keep every historical alert
#: rendering with the correct icon, colour and label.
CATEGORY_ALIASES: dict[str, str] = {
    "SECURITY": "ROBBERY",
    "ROBBERY/ARMED": "ROBBERY",
    "STEALING": "THEFT",
    "FIGHT": "ASSAULT",
    "ACCIDENT": "MEDICAL",
    "MEDICAL EMERGENCY": "MEDICAL",
    "AMBULANCE": "MEDICAL",
    "OTHER": "GENERAL",
}

#: Workflow states of an SOS alert, with their presentation metadata.
STATUS_META: dict[str, dict[str, str]] = {
    "PENDING": {
        "emoji": "🟡",
        "label": "Pending confirmation",
        "colour": "#b8860b",
        "help": "Inside the 5-second cancel window - not yet broadcast.",
    },
    "ACTIVE": {
        "emoji": "🔴",
        "label": "ACTIVE EMERGENCY",
        "colour": "#e11d1d",
        "help": "Broadcast to the Security Command Dashboard.",
    },
    "DISPATCHED": {
        "emoji": "🚓",
        "label": "Officer dispatched",
        "colour": "#14509b",
        "help": "A security officer is on the way.",
    },
    "RESOLVED": {
        "emoji": "✅",
        "label": "Resolved",
        "colour": "#0b6b3a",
        "help": "Emergency closed by the control room.",
    },
    "CANCELLED": {
        "emoji": "⚪",
        "label": "Cancelled",
        "colour": "#6b7280",
        "help": "Cancelled by the citizen during the countdown.",
    },
}

#: Statuses that the dashboard treats as "live" and must react to.
LIVE_STATUSES: tuple[str, ...] = ("ACTIVE", "DISPATCHED")

USER_TYPES: tuple[str, ...] = ("Shopper", "Staff")


def category_meta(key: str) -> dict[str, str]:
    """Return presentation metadata for an emergency category.

    Unknown values are resolved through :data:`CATEGORY_ALIASES` first (so that
    historical rows recorded as "SECURITY" still display correctly), and finally
    fall back to OTHER.
    """
    normalised = str(key or "").strip().upper()
    normalised = CATEGORY_ALIASES.get(normalised, normalised)
    return CATEGORIES.get(normalised, CATEGORIES["GENERAL"])


def status_meta(key: str) -> dict[str, str]:
    """Return presentation metadata for an alert status."""
    return STATUS_META.get((key or "").upper(), STATUS_META["ACTIVE"])


# ---------------------------------------------------------------------------
# 5. TIME HELPERS  (all timestamps are stored in West Africa Time)
# ---------------------------------------------------------------------------
def now_wat() -> datetime:
    """Current time in West Africa Time (UTC+1)."""
    return datetime.now(WAT)


def now_iso() -> str:
    """Current WAT timestamp as an ISO-8601 string (seconds resolution)."""
    return now_wat().isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string coming from SQLite, tolerating legacy rows."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WAT)
    return dt


def fmt_time(value: str | None, with_seconds: bool = True) -> str:
    """Format a stored timestamp as ``14:32:07`` for the dashboard."""
    dt = parse_iso(value)
    if dt is None:
        return "--:--"
    return dt.strftime("%H:%M:%S" if with_seconds else "%H:%M")


def fmt_datetime(value: str | None) -> str:
    """Format a stored timestamp as ``05/03/2026 14:32:07``."""
    dt = parse_iso(value)
    if dt is None:
        return "-"
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def fmt_ago(value: str | None) -> str:
    """Human friendly "time ago" string used on the live alert cards."""
    dt = parse_iso(value)
    if dt is None:
        return "-"
    seconds = int((now_wat() - dt).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s ago"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
    return f"{seconds // 86400}d ago"


def age_seconds(value: str | None) -> float:
    """Age of a timestamp in seconds (large number when unparsable)."""
    dt = parse_iso(value)
    if dt is None:
        return 9_999.0
    return max(0.0, (now_wat() - dt).total_seconds())


def fmt_distance(meters: float | None) -> str:
    """Pretty-print a Haversine distance in metres / kilometres."""
    if meters is None:
        return "unknown"
    meters = float(meters)
    if meters < 1000:
        return f"{meters:.1f} m"
    return f"{meters / 1000.0:.3f} km"


# ---------------------------------------------------------------------------
# 6. NETWORK / DEMO HELPERS
# ---------------------------------------------------------------------------
def lan_ip() -> str:
    """Best-effort detection of this computer's LAN IP address.

    Used so that the student can type the phone URL quickly on defence day
    (the phone must be on the same Wi-Fi network / hotspot as the laptop).
    No packets are actually transmitted by the UDP ``connect`` call.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.6)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:  # pragma: no cover - depends on host networking
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:  # pragma: no cover
        return "127.0.0.1"


# ---------------------------------------------------------------------------
# 7. ALARM AUDIO ENGINE  (the "wow factor" of the defence)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def alarm_base64() -> str:
    """Return the base64 payload of ``static/alarm.wav`` ("" when missing).

    Embedding the sound as a ``data:`` URI inside the component removes every
    external dependency: no CDN, no cloud storage, no paid API.  It also
    survives the browser's autoplay policy far better than a remote URL.
    """
    for candidate in (ALARM_WAV, ALARM_WAV_COPY):
        if candidate.exists() and candidate.stat().st_size > 1024:
            return base64.b64encode(candidate.read_bytes()).decode("ascii")
    return ""


def _siren_javascript() -> str:
    """JavaScript fallback that synthesises a siren with the Web Audio API.

    If the browser refuses to autoplay the WAV file (strict autoplay policy),
    the component synthesises a two-tone siren instead; this needs no audio
    file at all.  A listener on the parent document re-arms the sound on the
    first user gesture, which is what happens when the operator clicks
    "Login" or "Test Alarm".
    """
    return """
(function () {
  var audio  = document.getElementById('spar-alarm-audio');
  var sirenOn = false;
  var ctx = null, osc = null, gain = null, timer = null;

  function startSiren() {
    if (sirenOn) { if (ctx && ctx.state === 'suspended') { ctx.resume(); } return; }
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) { return; }
      sirenOn = true;
      ctx  = new Ctx();
      gain = ctx.createGain();
      gain.gain.value = 0.0001;
      gain.connect(ctx.destination);
      osc = ctx.createOscillator();
      osc.type = 'square';
      osc.frequency.value = 950;
      osc.connect(gain);
      osc.start();
      var high = true;
      timer = setInterval(function () {
        var t = ctx.currentTime;
        osc.frequency.setValueAtTime(high ? 950 : 640, t);
        gain.gain.setValueAtTime(0.30, t);
        high = !high;
      }, 400);
      if (ctx.state === 'suspended') { ctx.resume(); }
    } catch (e) { /* silently ignore */ }
  }

  function playAlarm() {
    if (sirenOn) { return; }
    if (!audio) { startSiren(); return; }
    try { audio.volume = 1.0; audio.loop = true; } catch (e) {}
    var p = audio.play();
    if (p && p.catch) {
      p.catch(function () { startSiren(); });
    }
  }

  playAlarm();
  document.addEventListener('click', playAlarm);
  document.addEventListener('touchstart', playAlarm);
  document.addEventListener('keydown', playAlarm);
  try {
    /* Same-origin parent: a click anywhere in the dashboard re-arms audio. */
    window.parent.document.addEventListener('click', playAlarm);
  } catch (e) { /* cross-origin: ignore */ }
})();
"""


def alarm_strip_html(ring: bool, detail: str = "", silent_hint: bool = False) -> str:
    """Build the HTML for the alarm strip (deterministic for a given state).

    Streamlit only reloads the iframe when the HTML *string changes*, so
    keeping this function deterministic means the siren keeps ringing
    seamlessly across the 3-second dashboard refreshes instead of restarting.
    """
    b64 = alarm_base64()

    if not ring:
        text = detail or "Alarm idle - no unacknowledged active alert."
        return f"""
<div style="font-family:'Segoe UI',system-ui,sans-serif;background:#e8f5ec;border:2px solid #0b6b3a;
            border-radius:12px;padding:10px 16px;color:#0b6b3a;font-weight:700;font-size:15px;
            display:flex;align-items:center;gap:10px;">
  <span style="font-size:20px;">🔕</span><span>{escape(text)}</span>
</div>"""

    label = escape(detail or "ACTIVE EMERGENCY - alarm sounding in the control room")
    silent_note = (
        '<span style="background:#111827;padding:3px 10px;border-radius:999px;font-size:12px;">'
        "🔇 citizen Silent Mode was ON for this alert</span>"
        if silent_hint
        else ""
    )
    sources = ""
    if b64:
        sources = (
            f'<source src="data:audio/wav;base64,{b64}" type="audio/wav">'
            '<source src="/app/static/alarm.wav" type="audio/wav">'
        )
    return f"""
<div style="font-family:'Segoe UI',system-ui,sans-serif;background:linear-gradient(90deg,#7f1d1d,#e11d1d,#7f1d1d);
            border:3px solid #450a0a;border-radius:12px;padding:10px 16px;color:#fff;font-weight:800;
            font-size:16px;display:flex;align-items:center;gap:12px;
            animation:sparAlarmFlash .8s infinite alternate;">
  <style>@keyframes sparAlarmFlash {{ from {{ filter:brightness(1); }} to {{ filter:brightness(1.45); }} }}</style>
  <span style="font-size:24px;">🔔</span>
  <span>{label}</span>
  {silent_note}
  <audio id="spar-alarm-audio" autoplay loop preload="auto" style="display:none;">{sources}</audio>
  <script>{_siren_javascript()}</script>
</div>"""


def render_alarm_strip(ring: bool, detail: str = "", silent_hint: bool = False) -> None:
    """Render the alarm strip (and therefore the siren) inside the dashboard."""
    components.html(
        alarm_strip_html(ring, detail, silent_hint),
        height=74 if ring else 54,
        scrolling=False,
    )


def feedback_html(vibrate: bool = True, beep: bool = False, message: str = "") -> str:
    """Haptic feedback for the citizen's phone after an SOS.

    SECURITY DECISION - NO SOUND ON THE CITIZEN'S HANDSET
    -----------------------------------------------------
    The victim's phone must never make a sound.  In an armed robbery, a
    kidnapping or a home invasion a sudden beep can be heard by the offender,
    reveal that the victim has already alerted the police, and put the victim
    in immediate danger.  The confirmation is therefore **vibration only**.

    The ``beep`` argument is kept in the signature for backwards compatibility
    but is deliberately ignored: no ``<audio>`` element is ever emitted here.
    The audible alarm belongs on the SECURITY DASHBOARD (see
    :func:`render_alarm_strip`), which is in the control room - not in the room
    with the offender.

    ``vibrate`` - ``navigator.vibrate`` pattern.  Haptics are silent, so they
    confirm the SOS without giving the victim away.
    """
    # No tone is ever produced on the citizen side.  See the docstring.
    tone = ""
    vib = ""
    if vibrate:
        vib = """
        <script>
          (function(){
            if (navigator.vibrate) { navigator.vibrate([300,120,300,120,600]); }
          })();
        </script>"""
    return f"""
<div style="font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;color:#374151;text-align:center;">
  {escape(message)}
</div>
{tone}{vib}"""


# ---------------------------------------------------------------------------
# 8. GLOBAL STYLESHEET
# ---------------------------------------------------------------------------
GLOBAL_CSS = """
<style>
/* ---------- general look and feel ---------- */
:root { --spar-red:#e11d1d; --spar-dark:#111827; }
#MainMenu, footer, [data-testid="stMainMenu"], [data-testid="stAppDeployButton"] {
    visibility: hidden; height: 0;
}
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
html, body, [class*="css"] { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }

/* ---------- citizen status bar ---------- */
.spar-statusbar {
    display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:center;
    background:#111827; color:#f9fafb; border-radius:14px; padding:10px 14px;
    font-size:15px; font-weight:600; margin-bottom:12px;
}
.spar-statusbar .dot-ok   { color:#4ade80; font-weight:800; }
.spar-statusbar .dot-warn { color:#fbbf24; font-weight:800; }
.spar-statusbar .sep      { opacity:.35; }

/* ---------- the ONE-TAP SOS BUTTON (half of the viewport, always first) ---- */
.st-key-sos_big button,
.st-key-sos_big button:focus {
    width:100% !important;
    height:48vh !important;
    min-height:270px !important;
    border-radius:34px !important;
    border:7px solid #7f1d1d !important;
    background:radial-gradient(circle at 50% 32%, #ff6b6b 0%, #e11d1d 46%, #8b0000 100%) !important;
    color:#ffffff !important;
    box-shadow:0 0 0 0 rgba(225,29,29,.85), 0 20px 45px rgba(139,0,0,.55) !important;
    animation:sosPulse 1.35s infinite !important;
    transition:transform .08s ease-in-out !important;
    white-space:pre-line !important;
}
.st-key-sos_big button p {
    font-size:2.35rem !important; font-weight:900 !important; line-height:1.3 !important;
    color:#ffffff !important; margin:0 !important; letter-spacing:1px !important;
    text-shadow:0 4px 14px rgba(0,0,0,.5) !important;
}
.st-key-sos_big button:active { transform:scale(.975) !important; }
.st-key-sos_big button:disabled {
    background:#4b5563 !important; border-color:#374151 !important;
    animation:none !important; box-shadow:none !important; opacity:.85 !important;
}
@keyframes sosPulse {
    0%   { box-shadow:0 0 0 0 rgba(225,29,29,.90), 0 20px 45px rgba(139,0,0,.55); }
    70%  { box-shadow:0 0 0 26px rgba(225,29,29,0), 0 20px 45px rgba(139,0,0,.55); }
    100% { box-shadow:0 0 0 0 rgba(225,29,29,0), 0 20px 45px rgba(139,0,0,.55); }
}

/* ---------- the six OPTIONAL one-tap event icons ---------- */
.st-key-cat_ROBBERY button, .st-key-cat_THEFT button, .st-key-cat_ASSAULT button,
.st-key-cat_MEDICAL button, .st-key-cat_FIRE button, .st-key-cat_GENERAL button {
    height:88px !important; border-radius:18px !important;
    font-size:.95rem !important; font-weight:800 !important;
    letter-spacing:.4px !important; white-space:pre-line !important;
    border-width:3px !important; padding:4px !important;
}
.st-key-cat_ROBBERY button p, .st-key-cat_THEFT button p, .st-key-cat_ASSAULT button p,
.st-key-cat_MEDICAL button p, .st-key-cat_FIRE button p, .st-key-cat_GENERAL button p {
    font-size:.95rem !important; font-weight:800 !important; margin:0 !important;
    line-height:1.3 !important;
}
/* selected icon = solid colour of that event ... */
.st-key-cat_ROBBERY button[kind="primary"],
.st-key-cat_ROBBERY button[data-testid="stBaseButton-primary"] {
    background:#b91c1c !important; border-color:#7f1d1d !important; color:#fff !important;
}
.st-key-cat_THEFT button[kind="primary"],
.st-key-cat_THEFT button[data-testid="stBaseButton-primary"] {
    background:#a16207 !important; border-color:#713f12 !important; color:#fff !important;
}
.st-key-cat_ASSAULT button[kind="primary"],
.st-key-cat_ASSAULT button[data-testid="stBaseButton-primary"] {
    background:#c2410c !important; border-color:#7c2d12 !important; color:#fff !important;
}
.st-key-cat_MEDICAL button[kind="primary"],
.st-key-cat_MEDICAL button[data-testid="stBaseButton-primary"] {
    background:#0b6b3a !important; border-color:#064a27 !important; color:#fff !important;
}
.st-key-cat_FIRE button[kind="primary"],
.st-key-cat_FIRE button[data-testid="stBaseButton-primary"] {
    background:#b3541e !important; border-color:#7d370f !important; color:#fff !important;
}
.st-key-cat_GENERAL button[kind="primary"],
.st-key-cat_GENERAL button[data-testid="stBaseButton-primary"] {
    background:#6d28d9 !important; border-color:#4c1d95 !important; color:#fff !important;
}
/* ... unselected icon = quiet outline, so the RED SOS button stays dominant */
.st-key-cat_ROBBERY button[kind="secondary"],
.st-key-cat_THEFT button[kind="secondary"],
.st-key-cat_ASSAULT button[kind="secondary"],
.st-key-cat_MEDICAL button[kind="secondary"],
.st-key-cat_FIRE button[kind="secondary"],
.st-key-cat_GENERAL button[kind="secondary"] {
    background:#f8fafc !important; color:#111827 !important; border-color:#cbd5e1 !important;
}

/* ---------- countdown (5-second cancel window) ---------- */
.spar-countdown-card {
    background:#111827; border:3px solid #fbbf24; border-radius:18px;
    padding:18px; text-align:center; color:#fff;
    animation:countdownPulse .7s infinite alternate;
}
@keyframes countdownPulse { from { border-color:#fbbf24; } to { border-color:#e11d1d; } }
.spar-countdown-number {
    font-size:4.2rem; font-weight:900; color:#fbbf24; line-height:1;
    font-variant-numeric:tabular-nums;
}

/* ---------- dashboard: flashing red border while an alert is live ---------- */
@keyframes sparBorderFlash {
    0%   { box-shadow:inset 0 0 0 0 rgba(225,29,29,.0); }
    50%  { box-shadow:inset 0 0 0 10px rgba(225,29,29,.85); }
    100% { box-shadow:inset 0 0 0 0 rgba(225,29,29,.0); }
}
.spar-emergency .stApp, .stApp.spar-emergency, body.spar-emergency .stApp {
    animation:sparBorderFlash 1.1s infinite;
}
div[data-testid="stAppViewContainer"].spar-emergency { animation:sparBorderFlash 1.1s infinite; }

/* ---------- dashboard cards ---------- */
.spar-alert-card {
    border-radius:14px; padding:14px 16px; margin-bottom:12px; color:#fff;
    box-shadow:0 8px 22px rgba(15,23,42,.22);
}
.spar-alert-card h4 { margin:0 0 6px 0; font-size:1.05rem; font-weight:800; letter-spacing:.4px; }
.spar-alert-card .row { font-size:13.5px; margin:3px 0; opacity:.97; }
.spar-alert-card .mono { font-variant-numeric:tabular-nums; font-weight:700; }
.spar-chip {
    display:inline-block; background:rgba(255,255,255,.22); border-radius:999px;
    padding:2px 10px; font-size:11.5px; font-weight:700; margin-right:6px;
}
.spar-kpi {
    background:#fff; border:1px solid #e5e7eb; border-left:6px solid #111827;
    border-radius:12px; padding:10px 12px; height:100%;
}
.spar-kpi .label { font-size:11.5px; text-transform:uppercase; letter-spacing:.8px; color:#6b7280; font-weight:700; }
.spar-kpi .value { font-size:1.75rem; font-weight:900; color:#111827; line-height:1.1; }
.spar-kpi .foot  { font-size:11.5px; color:#6b7280; }
.spar-banner {
    border-radius:14px; padding:14px 18px; font-weight:800; font-size:1.06rem;
    background:linear-gradient(90deg,#7f1d1d,#e11d1d); color:#fff; margin-bottom:14px;
}
.spar-login-card {
    background:#fff; border:1px solid #e5e7eb; border-radius:16px; padding:22px;
    box-shadow:0 12px 30px rgba(15,23,42,.10);
}
.spar-legend span { margin-right:14px; font-size:13px; font-weight:600; color:#374151; }
.spar-map-offline { border:1px solid #e5e7eb; border-radius:14px; overflow:hidden; background:#f8fafc; }

/* ---------- mobile tightening ---------- */
@media (max-width: 640px) {
    .st-key-sos_big button { height:44vh !important; border-radius:26px !important; }
    .st-key-sos_big button p { font-size:1.85rem !important; }
    .st-key-cat_ROBBERY button, .st-key-cat_THEFT button, .st-key-cat_ASSAULT button,
    .st-key-cat_MEDICAL button, .st-key-cat_FIRE button, .st-key-cat_GENERAL button {
        height:78px !important; font-size:.8rem !important;
    }
    .st-key-cat_ROBBERY button p, .st-key-cat_THEFT button p, .st-key-cat_ASSAULT button p,
    .st-key-cat_MEDICAL button p, .st-key-cat_FIRE button p, .st-key-cat_GENERAL button p {
        font-size:.8rem !important;
    }
    .spar-statusbar { font-size:13px; }
}
</style>
"""


def inject_global_css(extra: str = "") -> None:
    """Inject the shared stylesheet (plus any page specific overrides)."""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
    if extra:
        st.markdown(f"<style>{extra}</style>", unsafe_allow_html=True)


def _width_stretch_kwargs() -> dict[str, object]:
    """Return the "fill the container width" argument for this Streamlit build.

    Streamlit renamed ``use_container_width=True`` to ``width="stretch"`` in the
    1.50 series and will remove the old argument after 2025-12-31.  Resolving it
    once keeps this project warning-free on the installed build while remaining
    compatible with the older 1.37+ releases.
    """
    try:
        version = tuple(int(part) for part in st.__version__.split(".")[:2])
        if version >= (1, 50):
            return {"width": "stretch"}
    except Exception:  # pragma: no cover - defensive
        pass
    return {"use_container_width": True}


#: Use as ``st.button("...", **WIDTH_STRETCH)``.
WIDTH_STRETCH: dict[str, object] = _width_stretch_kwargs()


def emergency_border_css(active: bool) -> str:
    """Return CSS that makes the whole dashboard border flash red.

    Streamlit renders the application inside
    ``div[data-testid="stAppViewContainer"]``; an inset animated box-shadow on
    that container is the most reliable way to flash the entire viewport
    without touching Streamlit's internal React tree.
    """
    if not active:
        return ""
    return """
@keyframes sparAppFlash {
  0%   { box-shadow: inset 0 0 0 0 rgba(225,29,29,0.00); }
  45%  { box-shadow: inset 0 0 0 9px rgba(225,29,29,0.90); }
  100% { box-shadow: inset 0 0 0 0 rgba(225,29,29,0.00); }
}
div[data-testid="stAppViewContainer"] { animation: sparAppFlash 1.15s infinite; }
section[data-testid="stSidebar"] { border-right: 4px solid rgba(225,29,29,0.85); }
"""


# ---------------------------------------------------------------------------
# 9. OFFLINE SITE PLAN (graceful degradation when there is no internet)
# ---------------------------------------------------------------------------
#: Schematic layout of the Spar Hypermarket compound, expressed in metres
#: relative to the mall centre: (label, east_m, north_m, width_m, height_m, kind)
SITE_FEATURES: tuple[tuple[str, float, float, float, float, str], ...] = (
    ("SPAR Hypermarket (sales floor)", -110.0, -40.0, 175.0, 130.0, "building"),
    ("Cold Storage Room", -95.0, -68.0, 55.0, 42.0, "room"),
    ("Admin Block / Control Room", -35.0, -25.0, 50.0, 40.0, "room"),
    ("Checkout & Trolley Bay", 40.0, -20.0, 40.0, 70.0, "room"),
    ("Car Park (north)", -120.0, 50.0, 195.0, 60.0, "parking"),
    ("Loading Bay (west)", -170.0, -25.0, 45.0, 80.0, "parking"),
)

#: Roads drawn as straight polylines in metres: (name, [(east, north), ...])
SITE_ROADS: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = (
    ("Abakaliki Road", ((150.0, -180.0), (150.0, 170.0))),
    ("Access Drive", ((-10.0, 175.0), (-10.0, 112.0))),
    ("Service Lane", ((-185.0, -180.0), (-185.0, 170.0))),
)


def _svg_project(
    lat: float,
    lon: float,
    bounds: tuple[float, float, float, float],
    width: float,
    height: float,
) -> tuple[float, float]:
    """Project GPS coordinates into SVG pixel coordinates."""
    lat_min, lat_max, lon_min, lon_max = bounds
    x = (lon - lon_min) / (lon_max - lon_min) * width
    y = (lat_max - lat) / (lat_max - lat_min) * height
    return x, y


def site_plan_html(
    posts: list[dict] | None = None,
    officers: list[dict] | None = None,
    alerts: list[dict] | None = None,
    span_m: float = 230.0,
    height_px: int = 660,
) -> str:
    """Render a self-contained SVG "site plan" of the mall compound.

    This is the **offline fallback map**: it needs no tile server, no CDN and
    no internet connection at all, yet it still shows the geofence, every
    security post, every officer and a *pulsing* red marker for each active
    SOS alert (using native SVG animation).  It demonstrates graceful
    degradation - a first-class requirement for an emergency system that must
    never depend on the public internet.
    """
    posts = posts or []
    officers = officers or []
    alerts = alerts or []

    # ---- 1. Build a square bounding box centred on the mall ----------------
    d_lat = span_m / metres_per_degree_lat()
    d_lon = span_m / metres_per_degree_lon()
    bounds = (
        SPAR_CENTER_LAT - d_lat,
        SPAR_CENTER_LAT + d_lat,
        SPAR_CENTER_LON - d_lon,
        SPAR_CENTER_LON + d_lon,
    )

    W, H = 760.0, 760.0
    # metres -> pixels (the box is 2*span_m wide, mapped onto W pixels)
    m2px = W / (2.0 * span_m)

    def _xy(lat: float, lon: float) -> tuple[float, float]:
        return _svg_project(lat, lon, bounds, W, H)

    cx, cy = _xy(SPAR_CENTER_LAT, SPAR_CENTER_LON)
    geofence_r_px = GEOFENCE_RADIUS_M * m2px

    parts: list[str] = []
    add = parts.append

    add(f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="100%" '
        f'style="max-width:100%;display:block;background:#eef2f6;" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="Offline site plan of Spar Hypermarket Enugu">')

    add("""
<defs>
  <pattern id="grid" width="38" height="38" patternUnits="userSpaceOnUse">
    <path d="M 38 0 L 0 0 0 38" fill="none" stroke="#d7dee7" stroke-width="1"/>
  </pattern>
  <filter id="softshadow" x="-30%" y="-30%" width="160%" height="160%">
    <feDropShadow dx="0" dy="3" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.28"/>
  </filter>
</defs>""")
    add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="url(#grid)"/>')

    # ---- 2. Roads ----------------------------------------------------------
    for name, points in SITE_ROADS:
        coords = " ".join(
            f"{_xy(*offset_gps(SPAR_CENTER_LAT, SPAR_CENTER_LON, e, n))[0]:.1f},"
            f"{_xy(*offset_gps(SPAR_CENTER_LAT, SPAR_CENTER_LON, e, n))[1]:.1f}"
            for e, n in points
        )
        add(f'<polyline points="{coords}" fill="none" stroke="#b8c2cf" '
            f'stroke-width="26" stroke-linecap="round"/>')
        add(f'<polyline points="{coords}" fill="none" stroke="#f8fafc" '
            f'stroke-width="3" stroke-dasharray="12 10"/>')

    # ---- 3. Compound boundary + geofence ----------------------------------
    add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{geofence_r_px:.1f}" fill="rgba(225,29,29,0.05)" '
        f'stroke="#e11d1d" stroke-width="2.5" stroke-dasharray="10 7"/>')
    add(f'<text x="{cx:.1f}" y="{cy - geofence_r_px - 8:.1f}" text-anchor="middle" '
        f'font-family="Segoe UI,sans-serif" font-size="15" font-weight="700" fill="#b91c1c">'
        f'Geofence radius {GEOFENCE_RADIUS_M:.0f} m</text>')

    # ---- 4. Buildings ------------------------------------------------------
    for label, east, north, w_m, h_m, kind in SITE_FEATURES:
        x1, y1 = _xy(*offset_gps(SPAR_CENTER_LAT, SPAR_CENTER_LON, east, north + h_m))
        x2, y2 = _xy(*offset_gps(SPAR_CENTER_LAT, SPAR_CENTER_LON, east + w_m, north))
        fill = {"building": "#cbd5e1", "room": "#dfe6ee", "parking": "#e2e8f0"}[kind]
        stroke = {"building": "#64748b", "room": "#94a3b8", "parking": "#a8b3c2"}[kind]
        add(f'<rect x="{x1:.1f}" y="{y1:.1f}" width="{(x2 - x1):.1f}" height="{(y2 - y1):.1f}" '
            f'rx="6" fill="{fill}" stroke="{stroke}" stroke-width="2" filter="url(#softshadow)"/>')
        add(f'<text x="{(x1 + x2) / 2:.1f}" y="{(y1 + y2) / 2 + 4:.1f}" text-anchor="middle" '
            f'font-family="Segoe UI,sans-serif" font-size="13" font-weight="600" fill="#334155">'
            f'{escape(label)}</text>')

    # ---- 5. Security posts (blue) -----------------------------------------
    for post in posts:
        px, py = _xy(float(post["latitude"]), float(post["longitude"]))
        add(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="11" fill="#14509b" stroke="#ffffff" stroke-width="3"/>')
        add(f'<text x="{px:.1f}" y="{py + 4:.1f}" text-anchor="middle" font-size="11" '
            f'font-family="Segoe UI,sans-serif" fill="#ffffff" font-weight="700">P</text>')
        add(f'<text x="{px:.1f}" y="{py - 17:.1f}" text-anchor="middle" font-size="12.5" '
            f'font-family="Segoe UI,sans-serif" fill="#0b2f61" font-weight="700">'
            f'{escape(str(post.get("post_name", "Post")))}</text>')

    # ---- 6. Officers (green = available, orange = dispatched) -------------
    for idx, off in enumerate(officers):
        ox, oy = _xy(float(off["current_lat"]), float(off["current_long"]))
        busy = str(off.get("status", "AVAILABLE")).upper() != "AVAILABLE"
        colour = "#f59e0b" if busy else "#0b6b3a"
        # small fan-out so two officers at the same post do not overlap
        nudge = 16 * (idx % 2)
        add(f'<circle cx="{ox:.1f}" cy="{oy - nudge:.1f}" r="10" fill="{colour}" '
            f'stroke="#ffffff" stroke-width="3"/>')
        add(f'<text x="{ox:.1f}" y="{oy - nudge + 4:.1f}" text-anchor="middle" font-size="10.5" '
            f'font-family="Segoe UI,sans-serif" fill="#ffffff" font-weight="700">S</text>')
        add(f'<text x="{ox + 15:.1f}" y="{oy - nudge + 4:.1f}" font-size="12" '
            f'font-family="Segoe UI,sans-serif" fill="#14532d" font-weight="700">'
            f'{escape(str(off.get("name", "Officer")))}</text>')

    # ---- 7. ACTIVE alerts: pulsing red marker ------------------------------
    for alert in alerts:
        ax, ay = _xy(float(alert["latitude"]), float(alert["longitude"]))
        meta = category_meta(str(alert.get("emergency_type", "GENERAL")))
        add(f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="13" fill="#e11d1d">'
            f'<animate attributeName="r" values="13;34;13" dur="1.6s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0.85;0.05;0.85" dur="1.6s" repeatCount="indefinite"/>'
            f'</circle>')
        add(f'<circle cx="{ax:.1f}" cy="{ay:.1f}" r="12" fill="#e11d1d" stroke="#ffffff" stroke-width="3"/>')
        add(f'<text x="{ax:.1f}" y="{ay + 4:.1f}" text-anchor="middle" font-size="11" '
            f'font-family="Segoe UI,sans-serif" fill="#ffffff" font-weight="700">!</text>')
        add(f'<text x="{ax:.1f}" y="{ay - 20:.1f}" text-anchor="middle" font-size="13" '
            f'font-family="Segoe UI,sans-serif" fill="#7f1d1d" font-weight="800">'
            f'{meta["emoji"]} SOS - '
            f'{escape(str(alert.get("name") or alert.get("citizen_name") or "Citizen"))}</text>')

    # ---- 8. Centre marker --------------------------------------------------
    add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="#111827" stroke="#ffffff" stroke-width="2"/>')
    add(f'<text x="{cx + 10:.1f}" y="{cy + 5:.1f}" font-size="12.5" '
        f'font-family="Segoe UI,sans-serif" fill="#111827" font-weight="700">Mall centre 6.4521, 7.5089</text>')

    add('</svg>')

    legend = """
<div class="spar-legend" style="padding:10px 4px 0 4px;">
  <span>🔵 Security post</span><span>🟢 Officer available</span>
  <span>🟠 Officer on dispatch</span><span>🔴 Active SOS alert</span>
  <span>◌ 150 m geofence</span>
</div>"""

    return f"""<div class="spar-map-offline">{''.join(parts)}{legend}</div>"""


def render_site_plan(
    posts: list[dict] | None = None,
    officers: list[dict] | None = None,
    alerts: list[dict] | None = None,
    height_px: int = 660,
) -> None:
    """Render the offline SVG site plan inside a Streamlit component."""
    components.html(site_plan_html(posts, officers, alerts), height=height_px, scrolling=False)


# ---------------------------------------------------------------------------
# 10. SELF-TEST (``python utils.py``)
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    print("Haversine self-test")
    print("-" * 62)
    print(f"mall centre -> itself          : {haversine_distance_m(6.4521, 7.5089):.4f} m")
    print(f"Main Entrance post delta       : {haversine_distance_m(6.4522, 7.5088):.2f} m")
    print(f"Cold Storage post delta        : {haversine_distance_m(6.4515, 7.5080):.2f} m")
    print(f"1 deg of latitude at equator   : {haversine_distance_m(0, 0, 1, 0):.2f} m "
          f"(exact {metres_per_degree_lat():.2f} m)")
    print(f"antipodal (0,0)->(0,180)       : {haversine_distance_m(0, 0, 0, 180):.2f} m "
          f"(exact {EARTH_RADIUS_M * math.pi:.2f} m)")
    print(f"Enugu -> Lagos (approx)        : "
          f"{haversine_distance_m(6.4521, 7.5089, 6.5244, 3.3792) / 1000:.1f} km")
    print(f"alarm.wav present              : {ALARM_WAV.exists()} "
          f"({ALARM_WAV.stat().st_size if ALARM_WAV.exists() else 0} bytes)")
    print(f"LAN IP for the phone demo      : {lan_ip()}")
