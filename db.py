"""
db.py
=====
All SQLite persistence for the Mobile-Based Emergency Alert System.

Database file (100 % local, no cloud, no paid API)::

    data/spar_sos.db

Schema
------
1. ``users``             - pre-registered shoppers / staff (the "citizens").
2. ``sos_alerts``        - every SOS trigger, its GPS fix, Haversine distance,
                           workflow status and timestamps.
3. ``security_posts``    - fixed security posts inside the Spar Enugu compound.
4. ``security_officers`` - patrolling officers with their live GPS position.
5. ``dashboard_users``   - control-room logins (added for authentication;
                           passwords are stored as salted PBKDF2 hashes).
6. ``alert_events``      - append-only audit trail of every workflow action
                           (academic requirement: traceability / non-repudiation).

Concurrency notes
-----------------
The dashboard auto-refreshes every 3 seconds while the SOS application may be
writing at the very same moment, so the database runs in **WAL** journal mode
with a 15-second busy timeout.  Every function opens a short-lived connection,
which is the recommended pattern for Streamlit's multi-threaded script runner.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterable, Iterator, Sequence

from utils import (
    SPAR_CENTER_LAT,
    SPAR_CENTER_LON,
    USER_TYPES,
    WAT,
    DB_PATH,
    haversine_distance_m,
    is_inside_geofence,
    now_iso,
    now_wat,
)

# ---------------------------------------------------------------------------
# 1. CONNECTION HANDLING
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 120_000

SCHEMA_SQL = """
-- 1. CITIZENS -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    phone         TEXT    NOT NULL,
    user_type     TEXT    NOT NULL DEFAULT 'Shopper',
    silent_mode   INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT    NOT NULL
);

-- 2. SOS ALERTS -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS sos_alerts (
    alert_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL,
    emergency_type        TEXT    NOT NULL DEFAULT 'GENERAL',
    latitude              REAL    NOT NULL,
    longitude             REAL    NOT NULL,
    distance_from_center_m REAL   NOT NULL DEFAULT 0,
    status                TEXT    NOT NULL DEFAULT 'PENDING',
    created_at            TEXT    NOT NULL,
    resolved_at           TEXT,
    officer_id            INTEGER,
    dispatched_at         TEXT,
    note                  TEXT,
    FOREIGN KEY (user_id)    REFERENCES users(user_id),
    FOREIGN KEY (officer_id) REFERENCES security_officers(officer_id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_status  ON sos_alerts(status);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON sos_alerts(created_at DESC);

-- 3. SECURITY POSTS -------------------------------------------------------
CREATE TABLE IF NOT EXISTS security_posts (
    post_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    post_name TEXT NOT NULL,
    latitude  REAL NOT NULL,
    longitude REAL NOT NULL
);

-- 4. SECURITY OFFICERS ----------------------------------------------------
CREATE TABLE IF NOT EXISTS security_officers (
    officer_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    current_lat REAL NOT NULL,
    current_long REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'AVAILABLE',
    post_id     INTEGER,
    phone       TEXT,
    FOREIGN KEY (post_id) REFERENCES security_posts(post_id)
);

-- 5. CONTROL-ROOM LOGINS --------------------------------------------------
CREATE TABLE IF NOT EXISTS dashboard_users (
    dashboard_user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username          TEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,
    salt              TEXT NOT NULL,
    full_name         TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'Operator',
    created_at        TEXT NOT NULL
);

-- 6. AUDIT TRAIL ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT,
    actor      TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (alert_id) REFERENCES sos_alerts(alert_id)
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a short-lived SQLite connection with sane pragmas."""
    conn = sqlite3.connect(str(DB_PATH), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def _row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
    row = cursor.fetchone()
    return dict(row) if row else None


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """Context manager that commits (or rolls back) a write transaction."""
    with get_conn() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ---------------------------------------------------------------------------
# 2. PASSWORD HASHING (never store plaintext passwords)
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return ``(hex_digest, salt)`` using PBKDF2-HMAC-SHA256."""
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """Constant-time verification of a control-room password."""
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, password_hash)


def list_dashboard_users() -> list[dict[str, Any]]:
    """List control-room logins WITHOUT exposing hashes or salts."""
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT dashboard_user_id, username, full_name, role, created_at"
            " FROM dashboard_users ORDER BY dashboard_user_id"
        ))


def authenticate_dashboard_user(username: str, password: str) -> dict[str, Any] | None:
    """Return the dashboard user row when the credentials are valid."""
    with get_conn() as conn:
        row = _row(conn.execute(
            "SELECT * FROM dashboard_users WHERE username = ?", (username.strip(),)
        ))
    if not row:
        return None
    if verify_password(password, row["password_hash"], row["salt"]):
        return row
    return None


# ---------------------------------------------------------------------------
# 3. DATABASE CREATION AND SEEDING
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create every table (idempotent) and switch the file to WAL mode."""
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()


def db_is_seeded() -> bool:
    """True when the four reference tables already hold data."""
    try:
        with get_conn() as conn:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            posts = conn.execute("SELECT COUNT(*) AS c FROM security_posts").fetchone()["c"]
            dusers = conn.execute("SELECT COUNT(*) AS c FROM dashboard_users").fetchone()["c"]
        return users > 0 and posts > 0 and dusers > 0
    except sqlite3.Error:
        return False


# --- reference data actually used by the demo ------------------------------
SEED_USERS: tuple[tuple[str, str, str, int], ...] = (
    ("Amina Shopper", "0803 456 7890", "Shopper", 0),
    ("John SparStaff", "0806 123 4567", "Staff", 0),
    ("Ngozi Okafor", "0701 987 6543", "Shopper", 1),
    ("Emeka Sunday", "0812 345 6789", "Staff", 0),
)

SEED_POSTS: tuple[tuple[str, float, float], ...] = (
    ("Main Entrance", 6.4522, 7.5088),
    ("Parking Lot", 6.4525, 7.5092),
    ("Admin Block", 6.4519, 7.5085),
    ("Cold Storage", 6.4515, 7.5080),
)

#: (officer name, post index, GPS offset lat, GPS offset lon, phone)
SEED_OFFICERS: tuple[tuple[str, int, float, float, str], ...] = (
    ("Chinedu Okeke", 0, 6.45221, 7.50883, "0803 111 2233"),
    ("Ifeanyi Nwosu", 1, 6.45247, 7.50914, "0803 444 5566"),
    ("Fatima Bello", 2, 6.45192, 7.50854, "0803 777 8899"),
)

SEED_DASHBOARD_USERS: tuple[tuple[str, str, str, str], ...] = (
    ("spar_security", "spar2026", "Spar Security Command", "Administrator"),
    ("control_room", "control2026", "Control Room Supervisor", "Supervisor"),
)


def seed_reference_data() -> dict[str, int]:
    """Insert the reference/seed data. Safe to run repeatedly."""
    created = {"users": 0, "posts": 0, "officers": 0, "dashboard_users": 0, "alerts": 0}

    with _write() as conn:
        # ---- citizens -----------------------------------------------------
        for name, phone, user_type, silent in SEED_USERS:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE name = ? AND phone = ?", (name, phone)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO users (name, phone, user_type, silent_mode, registered_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (name, phone, user_type, silent, now_iso()),
                )
                created["users"] += 1

        # ---- security posts ----------------------------------------------
        for post_name, lat, lon in SEED_POSTS:
            exists = conn.execute(
                "SELECT 1 FROM security_posts WHERE post_name = ?", (post_name,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO security_posts (post_name, latitude, longitude) VALUES (?, ?, ?)",
                    (post_name, lat, lon),
                )
                created["posts"] += 1

        post_rows = _rows(conn.execute("SELECT * FROM security_posts ORDER BY post_id"))

        # ---- officers -----------------------------------------------------
        for name, post_index, lat, lon, phone in SEED_OFFICERS:
            exists = conn.execute(
                "SELECT 1 FROM security_officers WHERE name = ?", (name,)
            ).fetchone()
            if not exists:
                post_id = post_rows[post_index]["post_id"] if post_index < len(post_rows) else None
                conn.execute(
                    "INSERT INTO security_officers"
                    " (name, current_lat, current_long, status, post_id, phone)"
                    " VALUES (?, ?, ?, 'AVAILABLE', ?, ?)",
                    (name, lat, lon, post_id, phone),
                )
                created["officers"] += 1

        # ---- control-room logins -----------------------------------------
        for username, password, full_name, role in SEED_DASHBOARD_USERS:
            exists = conn.execute(
                "SELECT 1 FROM dashboard_users WHERE username = ?", (username,)
            ).fetchone()
            if not exists:
                digest, salt = hash_password(password)
                conn.execute(
                    "INSERT INTO dashboard_users"
                    " (username, password_hash, salt, full_name, role, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (username, digest, salt, full_name, role, now_iso()),
                )
                created["dashboard_users"] += 1

        # ---- historical (already closed) alerts for the analytics view -----
        user_rows = _rows(conn.execute("SELECT * FROM users ORDER BY user_id"))
        if user_rows and not conn.execute("SELECT 1 FROM sos_alerts LIMIT 1").fetchone():
            history = (
                (user_rows[0], "ROBBERY", 6.45225, 7.50905, 3.2, 210),
                (user_rows[1 % len(user_rows)], "MEDICAL", 6.45160, 7.50812, 4.5, 95),
                (user_rows[2 % len(user_rows)], "FIRE", 6.45248, 7.50870, 2.4, 0),
            )
            for user, etype, lat, lon, minutes_ago, response_s in history:
                created_dt = now_wat() - timedelta(minutes=minutes_ago)
                resolved_dt = created_dt + timedelta(seconds=response_s) if response_s else None
                distance = round(haversine_distance_m(lat, lon), 1)
                status = "RESOLVED" if response_s else "CANCELLED"
                cur = conn.execute(
                    "INSERT INTO sos_alerts (user_id, emergency_type, latitude, longitude,"
                    " distance_from_center_m, status, created_at, resolved_at, note)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user["user_id"], etype, lat, lon, distance, status,
                        created_dt.isoformat(timespec="seconds"),
                        resolved_dt.isoformat(timespec="seconds") if resolved_dt else None,
                        "Seed data - historical record for the analytics view",
                    ),
                )
                conn.execute(
                    "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
                    " VALUES (?, 'SEEDED', ?, 'setup_db.py', ?)",
                    (cur.lastrowid, f"Historical {etype} alert seeded", now_iso()),
                )
                created["alerts"] += 1

    return created


def reset_database() -> None:
    """Drop every table and recreate an empty schema (used by --reset)."""
    with get_conn() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS alert_events;
            DROP TABLE IF EXISTS sos_alerts;
            DROP TABLE IF EXISTS security_officers;
            DROP TABLE IF EXISTS dashboard_users;
            DROP TABLE IF EXISTS security_posts;
            DROP TABLE IF EXISTS users;
            """
        )
        conn.commit()
    init_db()


# ---------------------------------------------------------------------------
# 4. CITIZENS
# ---------------------------------------------------------------------------
def create_user(name: str, phone: str, user_type: str = "Shopper",
                silent_mode: bool = False) -> int:
    """Register (or re-use) a citizen and return the ``user_id``."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    user_type = user_type if user_type in USER_TYPES else USER_TYPES[0]

    with _write() as conn:
        existing = _row(conn.execute(
            "SELECT user_id FROM users WHERE phone = ? AND name = ?", (phone, name)
        ))
        if existing:
            conn.execute(
                "UPDATE users SET user_type = ?, silent_mode = ? WHERE user_id = ?",
                (user_type, int(bool(silent_mode)), existing["user_id"]),
            )
            return int(existing["user_id"])
        cur = conn.execute(
            "INSERT INTO users (name, phone, user_type, silent_mode, registered_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, phone, user_type, int(bool(silent_mode)), now_iso()),
        )
        return int(cur.lastrowid)


def get_user(user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)))


def list_users(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM users ORDER BY user_id DESC LIMIT ?", (limit,)
        ))


def default_citizen() -> dict[str, Any] | None:
    """Return the citizen whose profile is loaded automatically on the handset.

    The citizen application must open DIRECTLY on the red SOS button - a person
    in danger must never be shown a registration form.  The handset therefore
    starts with the pre-registered profile already loaded (exactly as a real
    installed app would remember its owner), and the citizen only has to press
    the button.  The lowest ``user_id`` (the first citizen registered at the
    mall) is used so that the default is stable and predictable.
    """
    with get_conn() as conn:
        row = _row(conn.execute(
            "SELECT * FROM users WHERE user_type = 'Shopper' ORDER BY user_id LIMIT 1"
        ))
        if row is None:
            row = _row(conn.execute("SELECT * FROM users ORDER BY user_id LIMIT 1"))
    return row


def set_user_silent_mode(user_id: int, silent: bool) -> None:
    with _write() as conn:
        conn.execute(
            "UPDATE users SET silent_mode = ? WHERE user_id = ?", (int(bool(silent)), user_id)
        )


# ---------------------------------------------------------------------------
# 5. SOS ALERTS - THE CORE WORKFLOW
# ---------------------------------------------------------------------------
def create_alert(
    user_id: int,
    emergency_type: str = "GENERAL",
    latitude: float = SPAR_CENTER_LAT,
    longitude: float = SPAR_CENTER_LON,
    status: str = "PENDING",
    note: str | None = None,
) -> int:
    """Insert a new SOS alert and return its ``alert_id``.

    The Haversine distance to the mall centre is computed **here**, at write
    time, and persisted in ``distance_from_center_m`` so that it is available
    for reporting even if the geo-reference point is changed later.
    """
    distance = round(haversine_distance_m(latitude, longitude), 1)
    created = now_iso()

    with _write() as conn:
        cur = conn.execute(
            "INSERT INTO sos_alerts (user_id, emergency_type, latitude, longitude,"
            " distance_from_center_m, status, created_at, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(user_id), (emergency_type or "GENERAL").upper(),
                float(latitude), float(longitude), distance, status, created, note,
            ),
        )
        alert_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
            " VALUES (?, 'CREATED', ?, 'citizen_app', ?)",
            (alert_id, f"{emergency_type} alert captured at {latitude:.5f}, {longitude:.5f} "
                       f"({distance:.1f} m from mall centre)", created),
        )
    return alert_id


def confirm_alert(alert_id: int, actor: str = "citizen_app") -> None:
    """Flip a PENDING alert to ACTIVE once the 5-second cancel window expires."""
    with _write() as conn:
        conn.execute(
            "UPDATE sos_alerts SET status = 'ACTIVE' WHERE alert_id = ? AND status = 'PENDING'",
            (alert_id,),
        )
        conn.execute(
            "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
            " VALUES (?, 'BROADCAST', 'Cancel window expired - alert broadcast to"
            " the Security Command Dashboard', ?, ?)",
            (alert_id, actor, now_iso()),
        )


def cancel_alert(alert_id: int, actor: str = "citizen_app",
                 reason: str = "Cancelled by citizen during the 5-second window") -> None:
    """Cancel an alert while it is still inside the cancel window."""
    with _write() as conn:
        conn.execute(
            "UPDATE sos_alerts SET status = 'CANCELLED', resolved_at = ?"
            " WHERE alert_id = ? AND status IN ('PENDING', 'ACTIVE')",
            (now_iso(), alert_id),
        )
        conn.execute(
            "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
            " VALUES (?, 'CANCELLED', ?, ?, ?)",
            (alert_id, reason, actor, now_iso()),
        )


def sweep_stale_pending(grace_seconds: int = 60) -> int:
    """Auto-cancel PENDING alerts abandoned by a closed browser tab.

    Defensive design: if a handset loses network or the browser is killed
    during the cancel window, the row must not sit in ``PENDING`` forever.
    """
    cutoff = (now_wat() - timedelta(seconds=grace_seconds)).isoformat(timespec="seconds")
    with _write() as conn:
        cur = conn.execute(
            "UPDATE sos_alerts SET status = 'CANCELLED', resolved_at = ?,"
            " note = COALESCE(note, '') || ' [auto-cancelled: cancel window abandoned]'"
            " WHERE status = 'PENDING' AND created_at < ?",
            (now_iso(), cutoff),
        )
        return cur.rowcount


def dispatch_alert(alert_id: int, officer_id: int | None = None,
                   actor: str = "spar_security") -> dict[str, Any] | None:
    """Dispatch the nearest available officer to an alert.

    Returns a dict describing the assignment (officer, distance, ETA) or
    ``None`` when no officer could be assigned.
    """
    alert = get_alert(alert_id)
    if not alert:
        return None

    assignment: dict[str, Any] | None = None

    with _write() as conn:
        officer: dict[str, Any] | None = None

        if officer_id:
            officer = _row(conn.execute(
                "SELECT * FROM security_officers WHERE officer_id = ?", (officer_id,)
            ))
        if officer is None:
            # ---- nearest-available-officer assignment (Haversine ranking) --
            candidates = _rows(conn.execute(
                "SELECT * FROM security_officers WHERE UPPER(status) = 'AVAILABLE'"
            ))
            if not candidates:
                candidates = _rows(conn.execute("SELECT * FROM security_officers"))
            if candidates:
                ranked = sorted(
                    candidates,
                    key=lambda o: haversine_distance_m(
                        alert["latitude"], alert["longitude"],
                        o["current_lat"], o["current_long"],
                    ),
                )
                officer = ranked[0]

        if not officer:
            return None

        distance = haversine_distance_m(
            alert["latitude"], alert["longitude"],
            officer["current_lat"], officer["current_long"],
        )
        eta = max(1, int(round(distance / 1.35)))  # 1.35 m/s walking pace

        conn.execute(
            "UPDATE sos_alerts SET status = 'DISPATCHED', officer_id = ?, dispatched_at = ?"
            " WHERE alert_id = ?",
            (officer["officer_id"], now_iso(), alert_id),
        )
        conn.execute(
            "UPDATE security_officers SET status = 'DISPATCHED' WHERE officer_id = ?",
            (officer["officer_id"],),
        )
        conn.execute(
            "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
            " VALUES (?, 'DISPATCHED', ?, ?, ?)",
            (
                alert_id,
                f"{officer['name']} dispatched - {distance:.1f} m away, "
                f"estimated response time {eta} s",
                actor, now_iso(),
            ),
        )

        assignment = {
            "officer_id": officer["officer_id"],
            "officer_name": officer["name"],
            "officer_phone": officer.get("phone"),
            "distance_m": round(distance, 1),
            "eta_seconds": eta,
        }

    return assignment


def resolve_alert(alert_id: int, actor: str = "spar_security",
                  note: str = "Emergency closed by the control room") -> None:
    """Close an alert and release the officer that was attached to it."""
    with _write() as conn:
        row = _row(conn.execute(
            "SELECT officer_id, status FROM sos_alerts WHERE alert_id = ?", (alert_id,)
        ))
        conn.execute(
            "UPDATE sos_alerts SET status = 'RESOLVED', resolved_at = ?, note = COALESCE(note, ?)"
            " WHERE alert_id = ?",
            (now_iso(), note, alert_id),
        )
        if row and row.get("officer_id"):
            conn.execute(
                "UPDATE security_officers SET status = 'AVAILABLE' WHERE officer_id = ?",
                (row["officer_id"],),
            )
        conn.execute(
            "INSERT INTO alert_events (alert_id, event, detail, actor, created_at)"
            " VALUES (?, 'RESOLVED', ?, ?, ?)",
            (alert_id, note, actor, now_iso()),
        )


# ---------------------------------------------------------------------------
# 6. ALERT QUERIES
# ---------------------------------------------------------------------------
ALERT_SELECT = """
SELECT a.*,
       u.name       AS citizen_name,
       u.phone      AS citizen_phone,
       u.user_type  AS citizen_type,
       u.silent_mode AS citizen_silent_mode,
       o.name       AS officer_name,
       o.phone      AS officer_phone,
       p.post_name  AS officer_post
FROM sos_alerts a
LEFT JOIN users u             ON u.user_id    = a.user_id
LEFT JOIN security_officers o ON o.officer_id = a.officer_id
LEFT JOIN security_posts p    ON p.post_id    = o.post_id
"""


def get_alert(alert_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute(ALERT_SELECT + " WHERE a.alert_id = ?", (alert_id,)))


def list_alerts(statuses: Sequence[str] | None = None, limit: int = 100,
                hours: int | None = None) -> list[dict[str, Any]]:
    """Return alerts (newest first), optionally filtered by status."""
    sql = ALERT_SELECT
    params: list[Any] = []
    clauses: list[str] = []

    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        clauses.append(f"UPPER(a.status) IN ({placeholders})")
        params.extend([s.upper() for s in statuses])

    if hours is not None:
        cutoff = (now_wat() - timedelta(hours=hours)).isoformat(timespec="seconds")
        clauses.append("a.created_at >= ?")
        params.append(cutoff)

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    # ACTIVE alerts always float to the top of the queue.
    sql += """
    ORDER BY CASE UPPER(a.status)
                 WHEN 'ACTIVE'     THEN 0
                 WHEN 'PENDING'    THEN 1
                 WHEN 'DISPATCHED' THEN 2
                 WHEN 'RESOLVED'   THEN 3
                 ELSE 4
             END,
             a.created_at DESC
    LIMIT ?
    """
    params.append(limit)

    with get_conn() as conn:
        return _rows(conn.execute(sql, params))


def live_alerts() -> list[dict[str, Any]]:
    """Alerts that the control room must currently act on (ACTIVE + DISPATCHED)."""
    return list_alerts(statuses=("ACTIVE", "DISPATCHED"), limit=60)


def active_alerts() -> list[dict[str, Any]]:
    """Only the un-acknowledged ACTIVE alerts (these drive the alarm)."""
    return list_alerts(statuses=("ACTIVE",), limit=60)


def pending_alerts() -> list[dict[str, Any]]:
    """Alerts still inside their 5-second cancel window."""
    return list_alerts(statuses=("PENDING",), limit=20)


def recent_alerts(hours: int = 12, limit: int = 200) -> list[dict[str, Any]]:
    return list_alerts(limit=limit, hours=hours)


def list_alert_events(alert_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM alert_events WHERE alert_id = ? ORDER BY event_id", (alert_id,)
        ))


# ---------------------------------------------------------------------------
# 7. SECURITY POSTS AND OFFICERS
# ---------------------------------------------------------------------------
def list_posts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute("SELECT * FROM security_posts ORDER BY post_id"))


def list_officers() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute(
            """
            SELECT o.*, p.post_name AS post_name,
                   COALESCE(p.latitude,  ?) AS post_lat,
                   COALESCE(p.longitude, ?) AS post_lon
            FROM security_officers o
            LEFT JOIN security_posts p ON p.post_id = o.post_id
            ORDER BY o.officer_id
            """,
            (SPAR_CENTER_LAT, SPAR_CENTER_LON),
        ))


def available_officers() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _rows(conn.execute(
            "SELECT * FROM security_officers WHERE UPPER(status) = 'AVAILABLE' ORDER BY officer_id"
        ))


def set_officer_status(officer_id: int, status: str) -> None:
    with _write() as conn:
        conn.execute(
            "UPDATE security_officers SET status = ? WHERE officer_id = ?",
            (status.upper(), officer_id),
        )


def move_officer(officer_id: int, lat: float, lon: float) -> None:
    """Simulate the officer moving towards an alert (progress bar on the map)."""
    with _write() as conn:
        conn.execute(
            "UPDATE security_officers SET current_lat = ?, current_long = ? WHERE officer_id = ?",
            (float(lat), float(lon), officer_id),
        )


def nearest_officer(lat: float, lon: float,
                    only_available: bool = True) -> dict[str, Any] | None:
    """Rank officers by Haversine distance and return the closest one."""
    officers = available_officers() if only_available else list_officers()
    if not officers:
        return None
    best = min(
        officers,
        key=lambda o: haversine_distance_m(lat, lon, o["current_lat"], o["current_long"]),
    )
    best = dict(best)
    best["distance_m"] = round(
        haversine_distance_m(lat, lon, best["current_lat"], best["current_long"]), 1
    )
    return best


def officer_position_table(lat: float, lon: float) -> list[dict[str, Any]]:
    """All officers with their distance from a point, nearest first."""
    rows: list[dict[str, Any]] = []
    for officer in list_officers():
        item = dict(officer)
        item["distance_m"] = round(
            haversine_distance_m(lat, lon, officer["current_lat"], officer["current_long"]), 1
        )
        item["inside_geofence"] = is_inside_geofence(officer["current_lat"], officer["current_long"])
        rows.append(item)
    return sorted(rows, key=lambda r: r["distance_m"])


# ---------------------------------------------------------------------------
# 8. ANALYTICS FOR THE DASHBOARD HEADER
# ---------------------------------------------------------------------------
def dashboard_stats() -> dict[str, Any]:
    """Aggregate counters used by the KPI strip."""
    with get_conn() as conn:
        def scalar(sql: str, params: Iterable[Any] = ()) -> int:
            row = conn.execute(sql, tuple(params)).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        active = scalar("SELECT COUNT(*) FROM sos_alerts WHERE UPPER(status) = 'ACTIVE'")
        dispatched = scalar("SELECT COUNT(*) FROM sos_alerts WHERE UPPER(status) = 'DISPATCHED'")
        pending = scalar("SELECT COUNT(*) FROM sos_alerts WHERE UPPER(status) = 'PENDING'")
        resolved_today = scalar(
            "SELECT COUNT(*) FROM sos_alerts WHERE UPPER(status) = 'RESOLVED' AND"
            " substr(resolved_at, 1, 10) = ?",
            (now_wat().strftime("%Y-%m-%d"),),
        )
        cancelled = scalar("SELECT COUNT(*) FROM sos_alerts WHERE UPPER(status) = 'CANCELLED'")
        total = scalar("SELECT COUNT(*) FROM sos_alerts")
        officers_available = scalar(
            "SELECT COUNT(*) FROM security_officers WHERE UPPER(status) = 'AVAILABLE'"
        )
        officers_total = scalar("SELECT COUNT(*) FROM security_officers")
        users_total = scalar("SELECT COUNT(*) FROM users")

        avg_row = conn.execute(
            """
            SELECT AVG((julianday(resolved_at) - julianday(created_at)) * 86400.0) AS avg_s
            FROM sos_alerts
            WHERE resolved_at IS NOT NULL AND UPPER(status) = 'RESOLVED'
            """
        ).fetchone()
        avg_response = float(avg_row["avg_s"]) if avg_row and avg_row["avg_s"] else 0.0

        farthest = conn.execute(
            "SELECT MAX(distance_from_center_m) AS m FROM sos_alerts"
        ).fetchone()
        max_distance = float(farthest["m"]) if farthest and farthest["m"] else 0.0

    return {
        "active": active,
        "dispatched": dispatched,
        "pending": pending,
        "resolved_today": resolved_today,
        "cancelled": cancelled,
        "total": total,
        "officers_available": officers_available,
        "officers_total": officers_total,
        "users_total": users_total,
        "avg_response_s": round(avg_response, 1),
        "max_distance_m": round(max_distance, 1),
    }


# ---------------------------------------------------------------------------
# 9. HEALTH CHECK (used by the startup banner and the smoke tests)
# ---------------------------------------------------------------------------
def health_check() -> dict[str, Any]:
    """Return a small dictionary describing the state of the database."""
    info: dict[str, Any] = {
        "path": str(DB_PATH),
        "exists": DB_PATH.exists(),
        "size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
    }
    try:
        with get_conn() as conn:
            info["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
            for table in ("users", "sos_alerts", "security_posts",
                          "security_officers", "dashboard_users", "alert_events"):
                info[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        info["ok"] = True
    except sqlite3.Error as exc:  # pragma: no cover
        info["ok"] = False
        info["error"] = str(exc)
    return info


if __name__ == "__main__":  # pragma: no cover
    init_db()
    print(health_check())
