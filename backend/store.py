"""Small SQLite store shared by the dashboard API and the Pipecat tools."""
from __future__ import annotations

import os
import uuid
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DATABASE_PATH", str(ROOT / "appointments.db")))
CLINIC_TZ = ZoneInfo("Asia/Kolkata")

DOCTOR_SEED = [
    ("dr-maya-shah", "Dr. Maya Shah", "Cardiology", "MS", "Calm, preventive heart care", "#d98970"),
    ("dr-arjun-mehta", "Dr. Arjun Mehta", "Dermatology", "AM", "Skin health, made simple", "#87977d"),
    ("dr-leena-iyer", "Dr. Leena Iyer", "Family medicine", "LI", "Your first call for everyday health", "#778ca3"),
    ("dr-kabir-rao", "Dr. Kabir Rao", "Orthopedics", "KR", "Movement and joint care", "#b99a68"),
]


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize() -> None:
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS doctors (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, specialty TEXT NOT NULL,
              initials TEXT NOT NULL, note TEXT NOT NULL, color TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slots (
              id TEXT PRIMARY KEY, doctor_id TEXT NOT NULL REFERENCES doctors(id),
              starts_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'available'
            );
            CREATE TABLE IF NOT EXISTS appointments (
              id TEXT PRIMARY KEY, doctor_id TEXT NOT NULL REFERENCES doctors(id),
              slot_id TEXT NOT NULL REFERENCES slots(id), phone TEXT NOT NULL,
              patient_name TEXT NOT NULL DEFAULT 'Caller', status TEXT NOT NULL DEFAULT 'booked',
              created_at TEXT NOT NULL
            );
        """)
        for doctor in DOCTOR_SEED:
            db.execute("INSERT OR IGNORE INTO doctors VALUES (?, ?, ?, ?, ?, ?)", doctor)
        count = db.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
        if count == 0:
            today = datetime.now(CLINIC_TZ).date()
            for day_offset in range(1, 8):
                day = today + timedelta(days=day_offset)
                if day.weekday() >= 5:
                    continue
                for index, doctor in enumerate(DOCTOR_SEED):
                    for hour, minute in [(9, 0), (10, 30), (13, 30), (15, 0)]:
                        starts = datetime.combine(day, time(hour, minute), tzinfo=CLINIC_TZ) + timedelta(minutes=index * 15)
                        slot_id = f"{doctor[0]}-{starts:%Y%m%d-%H%M}"
                        db.execute("INSERT OR IGNORE INTO slots(id, doctor_id, starts_at) VALUES (?, ?, ?)",
                                   (slot_id, doctor[0], starts.isoformat(timespec="minutes")))


def _doctor(db: sqlite3.Connection, doctor_id: str):
    row = db.execute("SELECT * FROM doctors WHERE id = ? OR lower(name) = lower(?) OR lower(specialty) = lower(?) LIMIT 1",
                     (doctor_id, doctor_id, doctor_id)).fetchone()
    if not row:
        row = db.execute("SELECT * FROM doctors WHERE lower(specialty) LIKE ? LIMIT 1", (f"%{doctor_id.lower()}%",)).fetchone()
    return row


def list_doctors():
    with connect() as db:
        result = []
        for row in db.execute("SELECT * FROM doctors ORDER BY name"):
            now = datetime.now(CLINIC_TZ).isoformat(timespec="minutes")
            slots = db.execute("SELECT id, starts_at FROM slots WHERE doctor_id=? AND status='available' AND starts_at>=? ORDER BY starts_at LIMIT 5", (row["id"], now)).fetchall()
            result.append({**dict(row), "slots": [dict(s) for s in slots]})
        return result


def list_slots(doctor_query: str = "", day: str = ""):
    with connect() as db:
        doctor = _doctor(db, doctor_query) if doctor_query else None
        if doctor_query and not doctor:
            return []
        sql = "SELECT s.*, d.name AS doctor_name, d.specialty FROM slots s JOIN doctors d ON d.id=s.doctor_id WHERE s.status='available' AND s.starts_at >= ?"
        args: list[str] = [datetime.now(CLINIC_TZ).isoformat(timespec="minutes")]
        if doctor:
            sql += " AND s.doctor_id=?"; args.append(doctor["id"])
        if day:
            sql += " AND substr(s.starts_at,1,10)=?"; args.append(day)
        sql += " ORDER BY s.starts_at LIMIT 80"
        return [dict(r) for r in db.execute(sql, args)]


def list_appointments():
    with connect() as db:
        rows = db.execute("""SELECT a.id, a.phone, a.patient_name, a.status, a.created_at,
          s.id AS slot_id, s.starts_at, d.id AS doctor_id, d.name AS doctor_name, d.specialty
          FROM appointments a JOIN slots s ON s.id=a.slot_id JOIN doctors d ON d.id=a.doctor_id
          WHERE a.status='booked' AND s.starts_at>=? ORDER BY s.starts_at""",
          (datetime.now(CLINIC_TZ).isoformat(timespec="minutes"),)).fetchall()
        return [dict(r) for r in rows]


def book(phone: str, doctor_query: str, slot_id: str = ""):
    with connect() as db:
        doctor = _doctor(db, doctor_query)
        if not doctor:
            return {"ok": False, "message": "I could not find that doctor or specialty."}
        now = datetime.now(CLINIC_TZ).isoformat(timespec="minutes")
        slot = db.execute("SELECT * FROM slots WHERE status='available' AND doctor_id=? AND starts_at>=? AND (id=? OR starts_at=?) ORDER BY starts_at LIMIT 1",
                          (doctor["id"], now, slot_id, slot_id)).fetchone() if slot_id else db.execute(
                              "SELECT * FROM slots WHERE status='available' AND doctor_id=? AND starts_at>=? ORDER BY starts_at LIMIT 1",
                              (doctor["id"], datetime.now(CLINIC_TZ).isoformat(timespec="minutes"))).fetchone()
        if not slot:
            return {"ok": False, "message": "That slot is no longer open. Please check availability again."}
        appointment_id = f"apt-{datetime.now(CLINIC_TZ):%y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        changed = db.execute("UPDATE slots SET status='booked' WHERE id=? AND status='available'", (slot["id"],)).rowcount
        if not changed:
            return {"ok": False, "message": "That slot was just taken. Please choose another."}
        db.execute("INSERT INTO appointments VALUES (?, ?, ?, ?, 'Caller', 'booked', ?)",
                   (appointment_id, doctor["id"], slot["id"], phone, datetime.now().isoformat(timespec="seconds")))
        return {"ok": True, "appointment_id": appointment_id, "doctor": doctor["name"],
                "specialty": doctor["specialty"], "starts_at": slot["starts_at"], "phone": phone}


def cancel(phone: str, appointment_id: str = ""):
    with connect() as db:
        sql = """SELECT a.id, s.id AS slot_id, s.starts_at, d.name AS doctor_name
          FROM appointments a JOIN slots s ON s.id=a.slot_id JOIN doctors d ON d.id=a.doctor_id
          WHERE a.phone=? AND a.status='booked'"""
        args: list[str] = [phone]
        if appointment_id:
            sql += " AND a.id=?"; args.append(appointment_id)
        sql += " ORDER BY s.starts_at LIMIT 1"
        row = db.execute(sql, args).fetchone()
        if not row:
            return {"ok": False, "message": "I could not find a current appointment for that number."}
        db.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (row["id"],))
        db.execute("UPDATE slots SET status='available' WHERE id=?", (row["slot_id"],))
        return {"ok": True, "doctor": row["doctor_name"], "starts_at": row["starts_at"], "phone": phone}

