# Spar Enugu Emergency Alert System

**Design and Implementation of a Mobile-Based Emergency Alert System using GPS**
*A Case Study of Spar Hypermarket, Enugu State*

A final-year Computer Science project: a two-mode Streamlit application that runs
from **one code base and one local SQLite database**, with **no cloud service and
no paid API of any kind**.

| Mode | Who uses it | Where it runs | URL |
|------|-------------|---------------|-----|
| **1 — One-Tap SOS App** | Shopper / staff (the citizen) | Phone browser | `/?mode=sos` |
| **2 — Security Command Dashboard** | Spar Enugu control room | Laptop browser | `/?mode=dashboard` |

Open both at the same time and you can demonstrate the complete end-to-end user
journey: the citizen taps SOS, the control room siren rings by itself, the
nearest officer is dispatched by the Haversine algorithm, and the citizen's
phone shows that help is on the way.

---

## 1. Quick start

```bat
setup_and_start.bat
```

The script does everything: finds the Python interpreter (it **re-uses the
existing environment** and only installs packages that are genuinely missing),
generates the alarm sound, creates and seeds `data/spar_sos.db`, picks a free
port, opens the dashboard and starts Streamlit.

Manual alternative:

```bat
python setup_db.py                     :: create + seed the database
python -m streamlit run app.py         :: start the application
```

Useful commands:

```bat
python setup_db.py --reset     :: rebuild the database from scratch
python setup_db.py --status    :: full verification report (screenshot this)
run_app.bat                    :: fast restart, skips the setup steps
run_app.bat 8505               :: force a specific port
```

### Control-room logins

| Username | Password | Role |
|---|---|---|
| `spar_security` | `spar2026` | Administrator |
| `control_room` | `control2026` | Supervisor |

Passwords are never stored in plain text — see §6.

---

## 2. Project folder structure

```
sparsos/
├── app.py                      Entry point: mode switcher + routing (162 lines)
├── views_sos.py                MODE 1 - the citizen one-tap SOS application
├── views_dashboard.py          MODE 2 - Security Command Dashboard + Folium map
├── db.py                       SQLite schema, seeding, queries, dispatch logic
├── utils.py                    Haversine formula, styling, alarm engine,
│                               offline SVG site plan
├── make_alarm.py               Synthesises static/alarm.wav (stdlib only)
├── setup_db.py                 Creates + seeds the database, prints a report
│
├── setup_and_start.bat         One-click setup and launch (Windows)
├── run_app.bat                 Fast restart for defence day
├── requirements.txt            streamlit, pandas, numpy, folium, streamlit-folium
├── DEMO_SCRIPT.txt             5-minute defence presentation script
├── README.md                   This file
│
├── .streamlit/
│   └── config.toml             Port, 0.0.0.0 binding, static serving, theme
│
├── data/
│   └── spar_sos.db             The live database (created by setup_db.py)
│
├── static/
│   └── alarm.wav               Siren, served at /app/static/alarm.wav
├── assets/
│   └── alarm.wav               Copy for playing in an external media player
│
└── tests/
    ├── __init__.py
    ├── test_haversine.py       Geospatial mathematics (16 tests)
    ├── test_database.py        Schema, workflow, dispatch, auth (25 tests)
    ├── test_app.py             End-to-end UI tests of both modes (29 tests)
    └── live_server_check.py    Launches the REAL server and checks both modes
```

---

## 3. The two modes

### Mode 1 — Shopper / Staff SOS App (`?mode=sos`)

**Strict UX rule: once the citizen is registered, this screen contains no text
input field at all.** A person in distress cannot type, so the emergency is
reduced to a single tap.

* **Onboarding** (shown once) — name, phone, Shopper/Staff, "Enable Location"
  and "Silent Mode" toggles. Four pre-registered citizens are offered as
  one-tap demo sign-in buttons so nothing has to be typed on the podium.
* **Status bar** — `👤 Name | 📍 Spar Hypermarket, Enugu | ● Location Active`
* **The button** — one massive red SOS button occupying roughly 60 % of the
  viewport, radial red gradient, white bold text, pulsing animation.
* **Categories** — three optional quick-tap icons: 🏥 Medical, 🔒 Security,
  🔥 Fire. If none is chosen the alert is sent as 🆘 GENERAL.
* **5-second cancel window** — a live countdown bar with one big CANCEL button.
  The window is implemented with a Streamlit *fragment* (`run_every=0.4`), so the
  CANCEL button stays responsive (a blocking `time.sleep` loop would freeze the
  very button the citizen needs most).
* **Silent Mode** — mutes every sound on the handset and uses vibration instead,
  which is what a robbery scenario demands. The control room is told that the
  citizen signalled quietly.
* **Success screen** — "✅ HELP IS ON THE WAY. Security has been alerted." plus
  the Haversine distance, the geofence decision and **live tracking** of the
  dispatch, refreshed every 3 seconds.

### Mode 2 — Security Command Dashboard (`?mode=dashboard`)

* **Login required** (PBKDF2-SHA256 hashed passwords).
* **Auto-refresh every 3 seconds** — implemented with
  `@st.fragment(run_every=3)`, which simulates real-time WebSocket ingestion
  without ever freezing the operator's mouse. A toggle falls back to manual
  refresh, and a "Refresh now" button is always available.
* **The alarm (the wow factor)** — the moment a new `ACTIVE` alert appears:
  * a loud siren plays automatically (base64 WAV embedded in a component),
  * a Web Audio two-tone siren takes over if the browser blocks the file,
  * the entire dashboard border flashes red,
  * a red banner appears, and a toast notification slides in,
  * a prominent **🔔 Silence Alarm** button is at the top right, and a NEW alert
    rings again even after an earlier one was silenced.
* **Map** — blue markers for the four security posts, green for available
  officers, orange for dispatched officers, purple for alerts still inside the
  cancel window, and a **red pulsing marker** for every ACTIVE SOS alert, with a
  dashed 150 m geofence circle. Two views are offered:
  * **📍 offline site plan** (default) — an SVG map drawn inside the application,
    so it works with **no internet at all**;
  * **🗺️ interactive Leaflet map** (`streamlit-folium`) with OpenStreetMap,
    CartoDB or Esri satellite tiles when a network is available.
* **Alert queue** — every live alert as a card showing citizen, phone, category,
  time, waiting time and the Haversine distance, with **🚓 Dispatch Officer**,
  **✅ Mark Resolved** and **🔕 Silence** actions.
* **Nearest-officer dispatch** — the system ranks every *available* officer with
  the Haversine function and assigns the closest one, reporting the distance and
  the estimated response time at 1.35 m/s walking pace.
* **Haversine working panel** — every alert card expands to show the formula
  evaluated with the real numbers (φ in radians, Δφ, Δλ, a, c, R·c) and the
  resulting geofence decision. This is the academic evidence the examiner asks
  for.
* **Audit trail** — every action (CREATED → BROADCAST → DISPATCHED → RESOLVED,
  or CANCELLED) is appended to `alert_events`.
* **Analytics** — KPI strip (active, dispatched, pending, resolved today, mean
  response time), officer deployment table, security-post geofence table and an
  alerts-by-category chart.

---

## 4. Database schema

`data/spar_sos.db` — SQLite in **WAL** journal mode, so the dashboard can read
every 3 seconds while the phone is writing.

```
users               (user_id, name, phone, user_type, silent_mode, registered_at)
sos_alerts          (alert_id, user_id, emergency_type, latitude, longitude,
                     distance_from_center_m, status, created_at, resolved_at,
                     officer_id, dispatched_at, note)
security_posts      (post_id, post_name, latitude, longitude)
security_officers   (officer_id, name, current_lat, current_long, status,
                     post_id, phone)
dashboard_users     (dashboard_user_id, username, password_hash, salt,
                     full_name, role, created_at)
alert_events        (event_id, alert_id, event, detail, actor, created_at)
```

`dashboard_users` and `alert_events` are additions to the four tables required by
the project document: they provide authentication and an audit trail. The extra
columns on `sos_alerts` (`officer_id`, `dispatched_at`, `note`) support the
dispatch workflow; every required column is present with its required name.

**Alert lifecycle**

```
tap SOS ──► PENDING ──(5 s, no cancel)──► ACTIVE ──► DISPATCHED ──► RESOLVED
               │
               └──(CANCEL pressed, or abandoned for 60 s)──► CANCELLED
```

### Seed data

| Citizens | `Amina Shopper`, `John SparStaff`, `Ngozi Okafor`, `Emeka Sunday` |
|---|---|
| **Security posts** | Main Entrance (6.4522, 7.5088) · Parking Lot (6.4525, 7.5092) · Admin Block (6.4519, 7.5085) · Cold Storage (6.4515, 7.5080) |
| **Officers** | Chinedu Okeke (Main Entrance) · Ifeanyi Nwosu (Parking Lot) · Fatima Bello (Admin Block) |
| **Logins** | `spar_security`/`spar2026` · `control_room`/`control2026` |
| **History** | three closed alerts (resolved/cancelled) so the analytics view is not empty — never ACTIVE, so the siren never rings on start-up |

---

## 5. The Haversine formula (academic core)

Implemented once in `utils.haversine_distance_m()` and used everywhere:
the alert card, the geofence test, the officer ranking and the audit trail.

```
φ₁, φ₂  = latitudes of the alert and of the mall centre   (radians)
Δφ = φ₂ − φ₁ ,  Δλ = λ₂ − λ₁

a = sin²(Δφ/2) + cos φ₁ · cos φ₂ · sin²(Δλ/2)
c = 2 · atan2(√a, √(1 − a))
d = R · c                    R = 6 371 008.8 m (mean Earth radius)
```

`atan2` is used instead of `asin` because it stays numerically stable at the
very short distances inside a mall compound (10–500 m).

Verified against exact sphere geometry in `tests/test_haversine.py`:

| Check | Expected | Result |
|---|---|---|
| 1° of latitude at the equator | R·π/180 = 111 195.08 m | ✅ within 1 m |
| antipodal points | π·R = 20 015 114.4 m | ✅ within 5 m |
| Main Entrance post | 15.7 m from the mall centre | ✅ |
| Car park (simulated fix) | 55.5 m | ✅ |
| Cold Storage | 119.7 m | ✅ |
| Loading bay | 171.8 m → **outside** the 150 m geofence | ✅ |
| 40 m east + 30 m north | equals the flat 50 m hypotenuse | ✅ Δ < 5 cm |

---

## 6. Security notes

* Passwords: PBKDF2-HMAC-SHA256, 120 000 iterations, per-user random salt,
  constant-time comparison (`hmac.compare_digest`). A test asserts the plain
  password never appears in the database.
* Every workflow action is recorded in `alert_events` with an actor
  (`citizen_app`, `spar_security`, `setup_db.py`) — non-repudiation.
* Defensive design: an alert abandoned inside its cancel window (browser closed,
  network lost) is automatically cancelled after 60 seconds so it can never
  masquerade as a live emergency.
* This is a local prototype. Production deployment would require HTTPS, role
  based permissions and a device-bound citizen identity.

---

## 7. Testing

```bat
python -m unittest discover -s tests -v        :: 70 automated tests
python -m unittest tests.test_haversine -v     :: mathematics only
python tests/live_server_check.py 8502         :: real-server end-to-end check
```

* `test_haversine.py` — 16 tests: analytical sphere values, symmetry,
  monotonicity, the flat-approximation agreement, the seeded post distances, the
  geofence boundary and the ETA calculation.
* `test_database.py` — 25 tests: schema, WAL mode, seeding idempotency, the full
  alert workflow, nearest-officer ranking, audit trail, abandoned-alert sweep,
  authentication (including a wrong-password and a plain-text-leak test).
* `test_app.py` — 29 tests driven by Streamlit's official `AppTest` harness:
  both modes render without exceptions, the dashboard rejects bad credentials,
  pressing the big button creates a PENDING row, cancelling leaves no live alert,
  an expired countdown broadcasts to ACTIVE, the dispatch button assigns the
  nearest officer, silence stops the siren — and an explicit test that the
  citizen screen contains **zero** text inputs.
* `live_server_check.py` — starts the real Streamlit server, opens a WebSocket
  session exactly as a browser does, requests both `?mode=dashboard` and
  `?mode=sos`, and asserts the expected content arrives with no exception.
  Result: **11/11 checks passed**.

---

## 8. Architecture decisions worth defending

1. **One code base, two modes.** The mode comes from the URL query string
   (`?mode=sos`) so a phone and a laptop can run different views against the same
   live database; a sidebar radio switches modes without editing the URL. No
   second server is started — Streamlit serves both.
2. **Fragments instead of blocking loops.** Streamlit fragments with
   `run_every` provide the 3-second live feed and the smooth countdown while
   keeping every button responsive. A `while True: sleep()` loop would have made
   the dashboard unusable.
3. **The alarm survives the browser autoplay policy.** The siren is a base64
   WAV inside an autoplaying looping `<audio>` element *plus* a Web Audio
   fallback, *plus* an explicit gesture re-arm on the parent document, *plus* a
   Test Alarm button. The alarm HTML is deterministic, so Streamlit does not
   reload the iframe every 3 seconds and the siren does not stutter.
4. **Never depend on the internet.** The default map is drawn in SVG inside the
   app; the Leaflet map is the optional extra, not the other way round.
5. **Graceful degradation everywhere.** Missing database → an on-screen button
   seeds it. Missing alarm file → it is regenerated at start-up. Location
   disabled → SOS is blocked with an explanation. No officer available → the
   closest officer is still assigned and the control room is told.

---

## 9. Requirements

Python 3.10+ with:

```
streamlit>=1.37     (fragments with run_every, query params, AppTest)
pandas>=2.0
numpy>=1.24
folium>=0.15
streamlit-folium>=0.18
```

`requirements.txt` pins the minimum versions that provide the features used.
`setup_and_start.bat` installs only what is missing.

---

## 10. Known limitations and future work

* The handset GPS fix is simulated for a reproducible demonstration; replacing
  it with `navigator.geolocation` (or an Android service) changes only the
  source of the numbers, not the processing.
* Officer positions are seeded rather than read from a real tracking device; the
  "simulate patrol movement" button in the dashboard shows how live positions
  would animate the map.
* A real deployment should sit behind HTTPS with role-based permissions, and
  would benefit from SMS/USSD fallback for citizens without data, plus an
  offline-first mobile client with a background retry queue.
"# sparsos" 
