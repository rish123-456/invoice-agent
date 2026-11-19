"""SQLite run ledger: every run, every stage, every decision is kept for audit
and powers the dashboard, duplicate detection and PO balance tracking."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).parent / "runs.db"


def _j(o):
    """numpy scalars -> python numbers; anything else -> str"""
    return o.item() if hasattr(o, "item") else str(o)


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS runs (
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT, file_name TEXT, vendor_id TEXT, vendor_name TEXT,
        invoice_number TEXT, invoice_key TEXT, invoice_date TEXT, po_number TEXT,
        subtotal REAL, total REAL, decision TEXT, reason TEXT,
        duration_ms INTEGER, stages_json TEXT, fields_json TEXT, actions_json TEXT)""")
    return c


def save_run(rec: dict) -> int:
    with conn() as c:
        cur = c.execute(
            """INSERT INTO runs (started_at,file_name,vendor_id,vendor_name,invoice_number,invoice_key,
               invoice_date,po_number,subtotal,total,decision,reason,duration_ms,stages_json,fields_json,actions_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["started_at"], rec["file_name"], rec.get("vendor_id"), rec.get("vendor_name"),
             rec.get("invoice_number"), rec.get("invoice_key"), rec.get("invoice_date"), rec.get("po_number"),
             rec.get("subtotal"), rec.get("total"), rec["decision"], rec["reason"], rec["duration_ms"],
             json.dumps(rec["stages"], default=_j), json.dumps(rec["fields"], default=_j),
             json.dumps(rec.get("actions", []))),
        )
        return cur.lastrowid


def all_runs() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY run_id DESC")]


def prior_invoices(vendor_id: str) -> list[dict]:
    """Invoices from this vendor that were accepted into the system (not rejected)."""
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM runs WHERE vendor_id=? AND decision!='REJECTED'", (vendor_id,))]


def billed_to_date(po_number: str) -> float:
    """Only APPROVED invoices consume PO balance; items in review do not."""
    with conn() as c:
        r = c.execute("SELECT COALESCE(SUM(subtotal),0) FROM runs WHERE po_number=? AND decision='APPROVED'",
                      (po_number,)).fetchone()
        return float(r[0])


def set_decision(run_id: int, decision: str, note: str):
    """Human override from the review queue -- kept in the audit trail."""
    with conn() as c:
        row = c.execute("SELECT stages_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
        stages = json.loads(row[0])
        stages.append({"stage": "Human review", "status": "done",
                       "detail": f"{decision} by reviewer: {note or '(no note)'}",
                       "at": datetime.now().isoformat(timespec="seconds")})
        c.execute("UPDATE runs SET decision=?, reason=?, stages_json=? WHERE run_id=?",
                  (decision, f"Reviewer: {note}" if note else "Reviewer decision", json.dumps(stages), run_id))


def reset():
    if DB.exists():
        DB.unlink()
