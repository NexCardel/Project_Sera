"""
drs.py
------
Deadline Reminder System (DRS) core engine.
Calculates current and previous filing periods, computes due dates, evaluates filing
statuses (Submitted, Pending, In-Progress, Overdue), and imports FPS JSON structures.
"""

import datetime
import json
import calendar
from typing import Optional, Dict, Any, List, Tuple


class DRSEngine:
    @staticmethod
    def resolve_schedule(filing_type: dict, variant_tag: str = None) -> dict:
        """
        Resolves the effective frequency, due_day, due_day_absolute, grace_days
        for a filing type given an optional variant tag (e.g. 'QRMP', 'AUDIT').
        """
        freq = filing_type.get("frequency", "monthly")
        due_day = filing_type.get("due_day")
        due_day_abs = filing_type.get("due_day_absolute")
        grace_days = filing_type.get("grace_days", 0)

        variants = filing_type.get("variants", [])
        if variant_tag and variants:
            for v in variants:
                if v.get("tag", "").upper() == variant_tag.upper():
                    if "frequency" in v:
                        freq = v["frequency"]
                    if "due_day" in v:
                        due_day = v["due_day"]
                    if "due_day_absolute" in v:
                        due_day_abs = v["due_day_absolute"]
                    if "grace_days" in v:
                        grace_days = v["grace_days"]
                    break

        return {
            "frequency": freq,
            "due_day": due_day,
            "due_day_absolute": due_day_abs,
            "grace_days": grace_days
        }

    @staticmethod
    def get_period_info(filing_type: dict, variant_tag: str = None, offset_periods: int = 0, ref_date: datetime.date = None) -> dict:
        """
        Calculates period label and due date for a filing type.
        offset_periods: 0 = current period, -1 = previous period, -2 = 2 periods ago, etc.
        """
        if ref_date is None:
            ref_date = datetime.date.today()

        schedule = DRSEngine.resolve_schedule(filing_type, variant_tag)
        freq = schedule["frequency"].lower()
        due_day = schedule["due_day"]
        due_day_abs = schedule["due_day_absolute"]

        year = ref_date.year
        month = ref_date.month

        if freq == "monthly":
            # For monthly filings: current period is the PREVIOUS month (e.g. in Aug, July return is being filed)
            target_month = month - 1 + offset_periods
            target_year = year
            while target_month < 1:
                target_month += 12
                target_year -= 1
            while target_month > 12:
                target_month -= 12
                target_year += 1

            period_label = f"{calendar.month_name[target_month]} {target_year}"

            # Due date: Nth day of the month FOLLOWING the target_month
            due_m = target_month + 1
            due_y = target_year
            if due_m > 12:
                due_m = 1
                due_y += 1
            
            day = due_day or 10
            max_days = calendar.monthrange(due_y, due_m)[1]
            due_date = datetime.date(due_y, due_m, min(day, max_days))

        elif freq == "quarterly":
            # Q1: Jan-Mar (due Apr), Q2: Apr-Jun (due Jul), Q3: Jul-Sep (due Oct), Q4: Oct-Dec (due Jan)
            q_num = (month - 1) // 3 + 1
            # Current filing period is usually the completed quarter
            target_q = q_num - 1 + offset_periods
            target_year = year
            while target_q < 1:
                target_q += 4
                target_year -= 1
            while target_q > 4:
                target_q -= 4
                target_year += 1

            q_names = {1: "Q1 (Jan-Mar)", 2: "Q2 (Apr-Jun)", 3: "Q3 (Jul-Sep)", 4: "Q4 (Oct-Dec)"}
            period_label = f"{q_names[target_q]} {target_year}"

            # Due date: month following the end of the quarter
            q_end_month = target_q * 3
            due_m = q_end_month + 1
            due_y = target_year
            if due_m > 12:
                due_m = 1
                due_y += 1

            day = due_day or 31
            max_days = calendar.monthrange(due_y, due_m)[1]
            due_date = datetime.date(due_y, due_m, min(day, max_days))

        elif freq == "annual":
            # Financial Year in India: Apr to Mar (e.g. 2025-26)
            if month >= 4:
                base_fy_start = year - 1
            else:
                base_fy_start = year - 2

            fy_start = base_fy_start + offset_periods
            period_label = f"FY {fy_start:04d}-{(fy_start % 100 + 1):02d}"

            # Absolute due date (e.g. "07-31" or "10-31" in the year following FY end)
            fy_end_year = fy_start + 1
            if due_day_abs and "-" in due_day_abs:
                m_str, d_str = due_day_abs.split("-")
                due_m = int(m_str)
                due_d = int(d_str)
                max_days = calendar.monthrange(fy_end_year, due_m)[1]
                due_date = datetime.date(fy_end_year, due_m, min(due_d, max_days))
            else:
                due_date = datetime.date(fy_end_year, 7, 31)

        else:
            period_label = f"{year:04d}"
            due_date = ref_date

        return {
            "period_label": period_label,
            "due_date": due_date.isoformat(),
            "due_date_formatted": due_date.strftime("%d %b %Y"),
            "grace_days": schedule["grace_days"]
        }

    @staticmethod
    def evaluate_status(db_status_record: Optional[dict], due_date_str: str, grace_days: int = 0, ref_date: datetime.date = None) -> str:
        """
        Determines visual status: 'submitted', 'in_progress', 'pending', or 'overdue'.
        """
        if ref_date is None:
            ref_date = datetime.date.today()

        if db_status_record and db_status_record.get("status"):
            st = db_status_record["status"].lower()
            if st in ("submitted", "in_progress"):
                return st

        due_date = datetime.date.fromisoformat(due_date_str)
        effective_deadline = due_date + datetime.timedelta(days=grace_days)

        if ref_date > effective_deadline:
            return "overdue"
        return "pending"


def import_fps_json(db, json_content: str, actor: str = "Admin") -> dict:
    """
    Parses and imports/upserts an FPS JSON payload into database `filing_types`.
    Matches `service_code`, `service`, or `service_name` against `services` table in DB.
    """
    data = json.loads(json_content)
    filing_types_input = (
        data.get("filing_types") or 
        data.get("filing_period_structures") or 
        (data if isinstance(data, list) else [])
    )

    raw_services = db.get_services()
    db_services = {s["name"].lower().strip(): s for s in raw_services}
    
    imported_count = 0
    updated_count = 0
    warnings = []

    for ft in filing_types_input:
        candidates = [
            ft.get("service_code"),
            ft.get("service"),
            ft.get("service_name")
        ]
        candidates = [c.strip().lower() for c in candidates if c and isinstance(c, str) and c.strip()]

        svc_id = None
        matched_svc_name = None

        # 1. Exact match
        for c in candidates:
            if c in db_services:
                svc_id = db_services[c]["id"]
                matched_svc_name = db_services[c]["name"]
                break

        # 2. Substring match fallback
        if svc_id is None:
            for c in candidates:
                for db_s_lower, db_svc in db_services.items():
                    if c in db_s_lower or db_s_lower in c:
                        svc_id = db_svc["id"]
                        matched_svc_name = db_svc["name"]
                        break
                if svc_id is not None:
                    break

        if svc_id is None:
            code_label = ft.get("code") or ft.get("sub_service_code") or "Unknown"
            disp_svc = ft.get("service_name") or ft.get("service") or ft.get("service_code") or "Unknown Service"
            warnings.append(f"Service '{disp_svc}' (for filing {code_label}) not found in database.")
            continue

        code = ft.get("code") or ft.get("sub_service_code")
        name = ft.get("name") or ft.get("sub_service_name") or code
        freq = ft.get("frequency", "monthly")
        start_period = ft.get("start_period", "2026-04-01")
        due_day = ft.get("due_day")
        due_day_abs = ft.get("due_day_absolute")
        grace_days = ft.get("grace_days", 0)
        notes = ft.get("notes", "")
        variants = ft.get("variants", [])

        if not code:
            warnings.append("Filing type entry missing code.")
            continue

        existing = db.get_filing_types(service_id=svc_id)
        is_update = any(e["code"].lower() == code.lower() for e in existing)

        db.upsert_filing_type(
            service_id=svc_id,
            code=code,
            name=name,
            frequency=freq,
            start_period=start_period,
            due_day=due_day,
            due_day_absolute=due_day_abs,
            grace_days=grace_days,
            notes=notes,
            variants=variants,
            imported_by=actor
        )

        if is_update:
            updated_count += 1
        else:
            imported_count += 1

    db.log_action(
        actor=actor,
        action="fps_import",
        detail=f"Imported {imported_count} new and updated {updated_count} filing types."
    )

    return {
        "imported": imported_count,
        "updated": updated_count,
        "warnings": warnings
    }
