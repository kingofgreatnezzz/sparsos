"""
setup_db.py
===========
One-shot setup script for the Spar Enugu Emergency Alert System.

It performs four jobs, in order:

1. Creates the SQLite database file ``data/spar_sos.db`` with the full schema.
2. Synthesises the alarm siren ``static/alarm.wav`` (pure Python, no download).
3. Seeds the reference data: 4 citizens, 4 security posts, 3 officers,
   2 control-room logins and 3 historical alerts for the analytics view.
4. Prints a verification report (row counts, logins, URLs) you can screenshot
   and paste into Chapter Four of your project document.

Usage
-----
    python setup_db.py             # create + seed (safe to re-run)
    python setup_db.py --reset     # DROP everything and start afresh
    python setup_db.py --status    # only print the current state
"""

from __future__ import annotations

import argparse
import sys

import db
from make_alarm import generate_alarm
from utils import (
    DB_PATH,
    GEOFENCE_RADIUS_M,
    SPAR_CENTER_LAT,
    SPAR_CENTER_LON,
    haversine_distance_m,
    lan_ip,
)

LINE = "=" * 74
RULE = "-" * 74


def banner(title: str) -> None:
    print()
    print(LINE)
    print(f"  {title}")
    print(LINE)


def report_status() -> bool:
    """Print a full verification report. Returns True when everything is fine."""
    health = db.health_check()
    banner("DATABASE VERIFICATION REPORT")

    if not health.get("ok"):
        print("  [FAIL] The database could not be opened:", health.get("error"))
        return False

    print(f"  Database file      : {health['path']}")
    print(f"  File size          : {health['size_bytes']:,} bytes")
    print(f"  Journal mode       : {health['journal_mode']} (WAL = safe for"
          f" simultaneous read/write)")
    print()
    print(f"  {'Table':<22}{'Rows':>6}   Contents")
    print("  " + RULE)
    contents = {
        "users": "citizens (shoppers and staff)",
        "sos_alerts": "SOS alerts (all statuses)",
        "security_posts": "fixed security posts in the compound",
        "security_officers": "security officers on patrol",
        "dashboard_users": "control-room logins (PBKDF2-hashed)",
        "alert_events": "audit-trail entries",
    }
    for table, label in contents.items():
        print(f"  {table:<22}{health.get(table, 0):>6}   {label}")

    print()
    print("  SECURITY POSTS AND HAVERSINE DISTANCE FROM THE MALL CENTRE")
    print("  " + RULE)
    print(f"  Geo-reference point : {SPAR_CENTER_LAT}, {SPAR_CENTER_LON}")
    print(f"  Geofence radius     : {GEOFENCE_RADIUS_M:.0f} m")
    for post in db.list_posts():
        distance = haversine_distance_m(post["latitude"], post["longitude"])
        inside = "inside" if distance <= GEOFENCE_RADIUS_M else "OUTSIDE"
        print(f"  - {post['post_name']:<16} ({post['latitude']:.4f}, {post['longitude']:.4f})"
              f"  =  {distance:6.1f} m  [{inside} geofence]")

    print()
    print("  SECURITY OFFICERS")
    print("  " + RULE)
    for officer in db.list_officers():
        distance = haversine_distance_m(officer["current_lat"], officer["current_long"])
        print(f"  - {officer['name']:<16} @ {str(officer.get('post_name') or 'Roaming'):<14}"
              f" status={officer['status']:<10} {distance:6.1f} m from centre")

    print()
    print("  CONTROL-ROOM LOGINS (dashboard mode)")
    print("  " + RULE)
    for row in db.list_dashboard_users():
        print(f"  - username: {row['username']:<16} role: {row['role']:<15} ({row['full_name']})")
    print("  - password for 'spar_security' is  spar2026   (change before real deployment)")
    print("  - password for 'control_room'  is  control2026")

    stats = db.dashboard_stats()
    print()
    print("  ALERT STATISTICS")
    print("  " + RULE)
    print(f"  Total alerts        : {stats['total']}")
    print(f"  Currently ACTIVE    : {stats['active']}")
    print(f"  Dispatched          : {stats['dispatched']}")
    print(f"  Resolved today      : {stats['resolved_today']}")
    print(f"  Average response    : {stats['avg_response_s']:.1f} seconds")
    print(f"  Officers available  : {stats['officers_available']} of {stats['officers_total']}")

    print()
    print("  DEFENCE-DAY URLS (open these two tabs)")
    print("  " + RULE)
    print(f"  Shopper SOS app   (phone)  : http://{lan_ip()}:8501/?mode=sos")
    print("  Security dashboard (laptop): http://localhost:8501/?mode=dashboard")
    print()
    print("  NOTE: the phone must be on the SAME Wi-Fi/hotspot as this laptop and")
    print("        Windows Firewall must allow Python on the private network.")
    print(LINE)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Set up and seed the Spar Enugu Emergency Alert System database."
    )
    parser.add_argument("--reset", action="store_true",
                        help="drop all tables and rebuild from scratch")
    parser.add_argument("--status", action="store_true",
                        help="only show the verification report")
    parser.add_argument("--quiet", action="store_true", help="minimal output")
    args = parser.parse_args(argv)

    if not args.quiet:
        banner("SPAR ENUGU EMERGENCY ALERT SYSTEM - SETUP")
        print("  Project : Design and Implementation of a Mobile-Based Emergency")
        print("            Alert System using GPS (a case study of Spar Hypermarket,")
        print("            Enugu State)")
        print("  Storage : SQLite (100% local - no cloud, no paid API)")

    # ---- 1. alarm sound ---------------------------------------------------
    alarm_path, alarm_size = generate_alarm()
    if not args.quiet:
        print()
        print(f"  [1/4] Alarm siren ready ......... {alarm_path.name} ({alarm_size:,} bytes)")

    # ---- 2. schema --------------------------------------------------------
    if args.reset:
        db.reset_database()
        if not args.quiet:
            print("  [2/4] Schema rebuilt ............ all tables dropped and recreated")
    else:
        db.init_db()
        if not args.quiet:
            print("  [2/4] Schema ready .............. data/spar_sos.db (WAL mode)")

    if args.status:
        return 0 if report_status() else 1

    # ---- 3. seed ----------------------------------------------------------
    created = db.seed_reference_data()
    if not args.quiet:
        print(f"  [3/4] Reference data seeded ..... {created['users']} citizens,"
              f" {created['posts']} posts, {created['officers']} officers,"
              f" {created['dashboard_users']} logins, {created['alerts']} historical alerts")
        if not any(created.values()):
            print("        (nothing new inserted - the database was already seeded)")

    # ---- 4. verify --------------------------------------------------------
    if not args.quiet:
        print("  [4/4] Verification .............. see report below")

    ok = report_status()
    if not args.quiet:
        print()
        print("  Setup complete. Start the application with:  streamlit run app.py")
        print(f"  (or double-click setup_and_start.bat)  DB file: {DB_PATH}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
