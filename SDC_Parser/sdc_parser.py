import os
import sys
import json
import re
import pandas as pd
from datetime import datetime
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side

DEBUG = True

# Setup paths to import sqlcipher3 and security module independently
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, APP_DIR)

try:
    import sqlcipher3.dbapi2 as sqlcipher
except ImportError:
    print("Error: sqlcipher3 not found. Make sure to run within the Sera Python environment.")
    sys.exit(1)

def get_db_hex_key():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.abspath(os.path.join(base_dir, ".."))

    salt_candidates = [
        os.path.join(app_dir, "sera.salt"),
        os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "sera.salt"),
        os.path.join(app_dir, "salt.bin"),
        os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "salt.bin"),
    ]
    key_candidates = [
        os.path.join(app_dir, "sera.key"),
        os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "sera.key"),
    ]

    salt = None
    for s_path in salt_candidates:
        if os.path.exists(s_path):
            try:
                with open(s_path, "rb") as f:
                    salt = f.read()
                if salt: break
            except Exception: pass

    pwd = "admin123"
    for k_path in key_candidates:
        if os.path.exists(k_path):
            try:
                with open(k_path, "r", encoding="utf-8") as f:
                    p = f.read().strip()
                if p:
                    pwd = p
                    break
            except Exception: pass
            
    if salt:
        try:
            if app_dir not in sys.path:
                sys.path.insert(0, app_dir)
            import security
            return security.derive_key_hex(pwd, salt)
        except Exception:
            pass
    return None

def get_db_connection():
    # Use the live database in AmanAssociates_Sera
    db_path = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera", "rawPayload.db")
    if not os.path.exists(db_path):
        db_path = os.path.join(APP_DIR, "rawPayload.db")
        if not os.path.exists(db_path):
            raise FileNotFoundError("rawPayload.db not found.")
            
    hex_key = get_db_hex_key()
    if not hex_key:
        raise ValueError("Could not derive database key")
        
    conn = sqlcipher.connect(db_path)
    conn.execute(f"PRAGMA key = \"x'{hex_key}'\";")
    conn.row_factory = sqlcipher.Row
    return conn

def evaluate_status(raw_status):
    raw = str(raw_status).lower().strip()
    # Strip any legacy bracketed color annotations
    raw = re.sub(r'[\(\[\{]\s*(?:green|yellow|red|blue|gray|grey)\s*[\)\]\}]', '', raw).strip()
    if not raw or raw == "null" or raw == "none":
        return "Not submitted"
    if "not filed" in raw or "unfiled" in raw or "to be filed" in raw:
        return "Not submitted"
    if "pending" in raw:
        return "Submitted (e-verification pending)"
    if "filed" in raw or "portal confirmed" in raw or "verified" in raw:
        return "Submitted & E-verified"
    elif "evc" in raw:
        return "Other EVC"
    elif "option expired" in raw:
        return "Option Expired (NA)"
    elif "landing" in raw or "form selected" in raw or "draft" in raw or "profile" in raw or raw == "na":
        return "Not submitted"
    if re.search(r'^(?:fy|due\s*date|status|na|-+)[\s\-:]*$', raw, re.I):
        return "Not submitted"
    return "Not submitted"

SKELETON_NAME_REGEX = re.compile(
    r'^(?:taxpayer|client|user|individual|indicates\s*mandatory\s*fields|mandatory\s*fields|goods\s+and\s+services\s+tax|gst\s+common\s+portal|gst\s+portal|status|due\s*date|fy|financial\s*year|tax\s*period|return\s*period|filing\s*period|legal\s*name|trade\s*name|gstin|pan|na|-+)[\s\-:*]*$',
    re.I
)

SKELETON_PERIOD_REGEX = re.compile(
    r'^(?:due\s*date|status|fy|financial\s*year|tax\s*period|return\s*period|filing\s*period|na|-+)[\s\-:]*$',
    re.I
)

def normalize_form_type(raw_form: str) -> str:
    if not raw_form:
        return ""
    f = str(raw_form).strip()
    if re.search(r'\b(GSTR[-_ ]*1A)\b', f, re.I):
        return "GSTR-1A"
    if re.search(r'\b(GSTR[-_ ]*1(?:\s*/\s*IFF)?|IFF)\b', f, re.I):
        return "GSTR-1/IFF"
    if re.search(r'\b(GSTR[-_ ]*2A)\b', f, re.I):
        return "GSTR-2A"
    if re.search(r'\b(GSTR[-_ ]*2B)\b', f, re.I):
        return "GSTR-2B"
    if re.search(r'\b(GSTR[-_ ]*3B[Q]?)\b', f, re.I):
        return "GSTR-3B"
    if re.search(r'\b(CMP[-_ ]*08)\b', f, re.I):
        return "CMP-08"
    if re.search(r'\b(GSTR[-_ ]*4)\b', f, re.I):
        return "GSTR-4"
    if re.search(r'\b(GSTR[-_ ]*9C)\b', f, re.I):
        return "GSTR-9C"
    if re.search(r'\b(GSTR[-_ ]*9)\b', f, re.I):
        return "GSTR-9"
    if re.search(r'\b(GSTR[-_ ]*7)\b', f, re.I):
        return "GSTR-7"
    if re.search(r'\b(GSTR[-_ ]*8)\b', f, re.I):
        return "GSTR-8"
    if re.search(r'\b(ITR[-_ ]*[1-7])\b', f, re.I):
        m = re.search(r'\b(ITR[-_ ]*[1-7])\b', f, re.I)
        return m.group(1).upper().replace(" ", "-")
    return f

def normalize_period(raw_period: str) -> str:
    if not raw_period:
        return "Unknown Period"
    p = str(raw_period).strip()
    p = re.sub(r'\s+', ' ', p)
    # Reject skeleton placeholders
    if SKELETON_PERIOD_REGEX.search(p):
        return "Unknown Period"
    # Split before status keywords if multi-line text was captured from table cells
    p = re.split(r'\s+(?:Filed|Not Filed|To be Filed|Pending|Option expired|Due date)\b', p, flags=re.I)[0].strip()
    if not p or SKELETON_PERIOD_REGEX.search(p):
        return "Unknown Period"
    return p

def process_timelines():
    print("Connecting to rawPayload.db to extract SDC Timelines...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # We order by start_time ASC so that newer sessions overwrite values for the same (PAN, GSTIN, Form, Filing Period)
        cur.execute("""
            SELECT session_id, pan, client_name, start_time, end_time, timeline_json
            FROM sdc_session_timelines
            ORDER BY start_time ASC
        """)
        rows = cur.fetchall()
    except Exception as e:
        print(f"Failed to read sdc_session_timelines: {e}")
        return []
    finally:
        conn.close()

    ltt_dict = {}

    for row in rows:
        session_id = row['session_id']
        timeline_json = row['timeline_json']
        if not timeline_json:
            continue
            
        try:
            timeline = json.loads(timeline_json)
        except Exception:
            continue
            
        if not timeline:
            continue
            
        start_url = timeline[0].get('url', '')
        start_time = timeline[0].get('timestamp', '')
        end_url = timeline[-1].get('url', '')
        end_time = timeline[-1].get('timestamp', '')
        site_history = f"Start: {start_url} ({start_time})\nEnd: {end_url} ({end_time})\nSteps: {len(timeline)}"
        
        # 1. Resolve session-wide identity (PAN, GSTIN, Names)
        session_pan = row['pan'] or ""
        session_full_name = ""
        session_temp_name = ""
        session_gstin = ""
        
        for step in timeline:
            cap = step.get('captured_data') or {}
            if cap.get('pan'):
                session_pan = cap.get('pan')
            if cap.get('gstin'):
                session_gstin = cap.get('gstin')
            trade_n = cap.get('company_name') or cap.get('trade_name') or ''
            prop_n = cap.get('proprietor_name') or cap.get('legal_name') or cap.get('client_name') or ''
            c_name = trade_n if (trade_n and not SKELETON_NAME_REGEX.search(trade_n)) else prop_n
            if c_name and not SKELETON_NAME_REGEX.search(c_name):
                if not session_full_name or len(c_name) >= len(session_full_name):
                    session_full_name = c_name
            t_name = cap.get('client_temp_name') or ''
            if t_name and not SKELETON_NAME_REGEX.search(t_name):
                if not session_temp_name or len(t_name) >= len(session_temp_name):
                    session_temp_name = t_name
                
        row_cname = row['client_name'] or ""
        if SKELETON_NAME_REGEX.search(row_cname):
            row_cname = ""
        final_name = session_full_name or session_temp_name or row_cname
        if SKELETON_NAME_REGEX.search(final_name):
            final_name = ""
        if not session_pan and session_gstin and len(session_gstin) >= 12:
            session_pan = session_gstin[2:12]
            
        if not session_pan:
            continue

        # 2. Extract distinct filings from the timeline
        # Group steps by (normalized_form, normalized_period) so multi-form/period sessions retain all entries
        filings_in_session = {}
        for step in timeline:
            cap = step.get('captured_data')
            if not cap:
                continue
            raw_form = (cap.get('form') or cap.get('filing_type') or "").strip()
            norm_form = normalize_form_type(raw_form)
            # Skip non-filing navigation steps and generic dashboard indicators
            if not norm_form or norm_form in ["Profile / Identity", "ITR (Landing / e-File)", "GST Return Filing", "Filing Dashboard"]:
                continue
            if "returns calendar" in norm_form.lower():
                continue

            raw_period = (cap.get('ay') or cap.get('period_label') or cap.get('period') or "").strip()
            norm_period = normalize_period(raw_period)
            status_raw = cap.get('status') or ""
            due_date = cap.get('due_date') or ""
            step_gstin = cap.get('gstin') or session_gstin
            step_time = step.get('timestamp') or end_time or start_time
            
            sub_key = (norm_form, norm_period)
            if sub_key not in filings_in_session:
                filings_in_session[sub_key] = {
                    "form": norm_form,
                    "period": norm_period,
                    "status_raw": status_raw,
                    "due_date": due_date,
                    "gstin": step_gstin,
                    "time": step_time
                }
            else:
                existing_f = filings_in_session[sub_key]
                if status_raw:
                    existing_f["status_raw"] = status_raw
                if due_date:
                    existing_f["due_date"] = due_date
                if step_gstin:
                    existing_f["gstin"] = step_gstin
                existing_f["time"] = step_time

        # Prune generic placeholder periods if a specific period exists for the same form
        forms_with_specific_period = {
            f_type for (f_type, p_label) in filings_in_session
            if p_label not in ("", "Current Period", "Unknown Period")
        }
        for (f_type, p_label) in list(filings_in_session.keys()):
            if p_label in ("Current Period", "Unknown Period") and f_type in forms_with_specific_period:
                del filings_in_session[(f_type, p_label)]
            elif p_label in ("", "Current Period", "Unknown Period"):
                f_entry = filings_in_session[(f_type, p_label)]
                if evaluate_status(f_entry.get("status_raw")) == "Not submitted" and not f_entry.get("due_date"):
                    del filings_in_session[(f_type, p_label)]

        # If no actual return filings occurred in this session, do not generate a phantom record
        if not filings_in_session:
            continue

        for (f_type, p_label), f_data in filings_in_session.items():
            filing_period = f_data["period"] if f_data["period"] else "Unknown Period"
            filing_type = f_data["form"]
            item_gstin = f_data["gstin"] or session_gstin
            submit_status = evaluate_status(f_data["status_raw"])
            
            key = (session_pan, item_gstin, filing_type, filing_period)
            
            if key not in ltt_dict:
                ltt_dict[key] = {
                    "PAN": session_pan,
                    "GSTIN": item_gstin,
                    "Client Name": final_name,
                    "Filing Period": filing_period,
                    "Filing Type": filing_type,
                    "Submit Status": submit_status,
                    "Due Date": f_data["due_date"],
                    "Session ID": session_id,
                    "Site History": site_history,
                    "Last Updated": f_data["time"]
                }
            else:
                existing = ltt_dict[key]
                if f_data["status_raw"]:
                    existing["Submit Status"] = submit_status
                if f_data["due_date"]:
                    existing["Due Date"] = f_data["due_date"]
                existing["Session ID"] = session_id
                existing["Site History"] = site_history
                existing["Last Updated"] = f_data["time"]
                if session_full_name and (not existing["Client Name"] or existing["Client Name"] == session_temp_name):
                    existing["Client Name"] = session_full_name

    # 3. Merge entries from tracker_dump table so live and individually captured filings are always included
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, client_id, portal, period_label, arn_number, capture_method, status, raw_payload_json, created_at
            FROM tracker_dump
            ORDER BY id ASC
        """)
        td_rows = cur.fetchall()
        for r in td_rows:
            raw_json = r['raw_payload_json'] or '{}'
            try:
                p_data = json.loads(raw_json)
            except Exception:
                p_data = {}
            raw_p = p_data.get('raw_payload') or {}
            
            # Expand the complete session collection so multi-form sessions
            # (e.g. GSTR-1 + GSTR-3B) are all represented in LTT, even when
            # tracker_dump selectively materializes only submitted/last-viewed
            # candidates. Prefer the explicit LTT collection when present;
            # assembler_captures remains the backward-compatible fallback.
            captures_to_process = raw_p.get('ltt_captures') or raw_p.get('assembler_captures')
            if not captures_to_process or not isinstance(captures_to_process, list):
                captures_to_process = [p_data]

            for item in captures_to_process:
                if not isinstance(item, dict):
                    continue
                t_pan = (item.get('pan') or p_data.get('pan') or raw_p.get('pan') or '').strip().upper()
                t_gstin = (item.get('gstin') or p_data.get('gstin') or raw_p.get('gstin') or '').strip().upper()
                if not t_pan and t_gstin and len(t_gstin) >= 12:
                    t_pan = t_gstin[2:12]
                if not t_pan:
                    continue

                t_name = item.get('proprietor_name') or item.get('legal_name') or item.get('client_name') or item.get('name') or p_data.get('proprietor_name') or p_data.get('legal_name') or p_data.get('client_name') or raw_p.get('proprietor_name') or raw_p.get('client_name') or ''
                if SKELETON_NAME_REGEX.search(t_name):
                    t_name = ""
                raw_period_val = item.get('period_label') or item.get('period') or p_data.get('period_label') or r['period_label'] or ''
                t_period = normalize_period(raw_period_val)
                t_form = normalize_form_type(item.get('filing_type') or item.get('form') or p_data.get('filing_type') or r['portal'] or '')
                if not t_form or t_form in ["Profile / Identity", "ITR (Landing / e-File)", "GST Return Filing", "Filing Dashboard"]:
                    continue
                if "returns calendar" in t_form.lower():
                    continue
                t_status = evaluate_status(item.get('status') or r['status'] or p_data.get('status') or '')
                if t_period in ("Current Period", "Unknown Period", "Due Date -", "Status -", "FY -") and t_status in ("Not submitted", "Initiated", "FY -"):
                    continue
                t_due = item.get('due_date') or p_data.get('due_date') or ''
                t_key = (t_pan, t_gstin, t_form, t_period)

                if t_key not in ltt_dict:
                    ltt_dict[t_key] = {
                        "PAN": t_pan,
                        "GSTIN": t_gstin,
                        "Client Name": t_name,
                        "Filing Period": t_period if t_period else "Unknown Period",
                        "Filing Type": t_form,
                        "Submit Status": t_status,
                        "Due Date": t_due,
                        "Session ID": item.get('session_id') or p_data.get('session_id') or f"TD-{r['id']}",
                        "Site History": f"Captured via {item.get('capture_method') or r['capture_method']}",
                        "Last Updated": item.get('last_viewed_at') or item.get('updated_at') or r['created_at']
                    }
                else:
                    existing = ltt_dict[t_key]
                    if t_status and t_status != "Not submitted":
                        existing["Submit Status"] = t_status
                    if t_due:
                        existing["Due Date"] = t_due
                    if t_name and (not existing["Client Name"] or SKELETON_NAME_REGEX.search(existing["Client Name"])):
                        existing["Client Name"] = t_name
        conn.close()
    except Exception as e:
        print(f"Failed to merge tracker_dump into LTT: {e}")

    return list(ltt_dict.values())

def generate_ltt_excel():
    data = process_timelines()
    if not data:
        print("No SDC timeline data found or extracted.")
        return
        
    df = pd.DataFrame(data)
    
    cols = ["PAN", "GSTIN", "Client Name", "Filing Period", "Filing Type", "Submit Status", "Due Date", "Session ID", "Last Updated", "Site History"]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]
    
    df['Sort Time'] = pd.to_datetime(df['Last Updated'], errors='coerce')
    df = df.sort_values(by=["Sort Time"], ascending=False) # Newest first
    df = df.drop(columns=['Sort Time'])
    
    live_dir = os.path.join(os.path.expanduser("~"), "AmanAssociates_Sera")
    os.makedirs(live_dir, exist_ok=True)
    output_file = os.path.join(live_dir, "Live_Tracking_Table_LTT.xlsx")
    
    def format_worksheet_columns(ws):
        ws.column_dimensions['A'].width = 15  # PAN
        ws.column_dimensions['B'].width = 18  # GSTIN
        ws.column_dimensions['C'].width = 30  # Client Name
        ws.column_dimensions['D'].width = 15  # Filing Period
        ws.column_dimensions['E'].width = 15  # Filing Type
        ws.column_dimensions['F'].width = 38  # Submit Status
        ws.column_dimensions['G'].width = 20  # Due Date
        ws.column_dimensions['H'].width = 25  # Session ID
        ws.column_dimensions['I'].width = 25  # Last Updated
        ws.column_dimensions['J'].width = 80  # Site History

        # Locate "Submit Status" column
        status_col = 6
        for c_idx in range(1, ws.max_column + 1):
            header_val = str(ws.cell(row=1, column=c_idx).value or '').strip().lower()
            if header_val == "submit status":
                status_col = c_idx
                break

        # High-contrast, WCAG-compliant style definitions:
        # Soft pastel background + bold dark text for maximum readability & professional appearance
        style_verified = {
            "fill": PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),  # Light pastel green
            "font": Font(color="145A32", bold=True, name="Calibri", size=11)                  # Dark forest green (8.2:1 contrast)
        }
        style_pending = {
            "fill": PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"),  # Light warm amber
            "font": Font(color="7D6608", bold=True, name="Calibri", size=11)                  # Dark amber/brown (6.4:1 contrast)
        }
        style_not_submitted = {
            "fill": PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"),  # Soft light red/rose
            "font": Font(color="78281F", bold=True, name="Calibri", size=11)                  # Deep burgundy/red (8.5:1 contrast)
        }
        style_expired = {
            "fill": PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid"),  # Light neutral gray
            "font": Font(color="595959", bold=True, name="Calibri", size=11)                  # Slate dark gray (5.6:1 contrast)
        }
        style_evc = {
            "fill": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),  # Light pastel blue
            "font": Font(color="1B4F72", bold=True, name="Calibri", size=11)                  # Deep navy blue (7.8:1 contrast)
        }

        cell_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9')
        )

        for row_idx in range(2, ws.max_row + 1):
            cell = ws.cell(row=row_idx, column=status_col)
            val = str(cell.value or '').strip()
            # Strip any legacy bracketed color labels if present
            cleaned_val = re.sub(r'\s*[\(\[\{]\s*(?:green|yellow|red|blue|gray|grey)\s*[\)\]\}]', '', val, flags=re.I).strip()
            cell.value = cleaned_val
            val_lower = cleaned_val.lower()

            target_style = None
            if "verified" in val_lower or "filed" in val_lower:
                target_style = style_verified
            elif "pending" in val_lower:
                target_style = style_pending
            elif "not submitted" in val_lower or "unfiled" in val_lower:
                target_style = style_not_submitted
            elif "expired" in val_lower or "na" in val_lower:
                target_style = style_expired
            elif "evc" in val_lower:
                target_style = style_evc

            if target_style:
                cell.fill = target_style["fill"]
                cell.font = target_style["font"]

            cell.border = cell_border
            cell.alignment = Alignment(vertical="center")

    target_out = output_file
    try:
        writer = pd.ExcelWriter(target_out, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Live Tracking Table (LTT)')
        format_worksheet_columns(writer.sheets['Live Tracking Table (LTT)'])
        writer.close()
    except PermissionError:
        target_out = os.path.join(live_dir, f"Live_Tracking_Table_LTT_{datetime.now().strftime('%H%M%S')}.xlsx")
        writer = pd.ExcelWriter(target_out, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Live Tracking Table (LTT)')
        format_worksheet_columns(writer.sheets['Live Tracking Table (LTT)'])
        writer.close()

    print(f"Success! Generated {target_out} with {len(data)} records.")
    return target_out

if __name__ == '__main__':
    print("--- SDC Parser (Live Tracking Table) ---")
    generate_ltt_excel()
