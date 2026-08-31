import os
import sys
import json
import pandas as pd
from datetime import datetime

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
    if not raw or raw == "null" or raw == "none":
        return "Not submitted"
    if "filed" in raw or "portal confirmed" in raw:
        return "Submitted & E-verified (green)"
    elif "pending" in raw:
        return "Submitted (e-verification pending) [yellow]"
    elif "evc" in raw:
        return "Other EVC"
    elif "option expired" in raw:
        return "Option Expired (NA)"
    elif "landing" in raw or "form selected" in raw or "draft" in raw or "profile" in raw or raw == "na":
        return "Not submitted"
    return "Not submitted"
def process_timelines():
    print("Connecting to rawPayload.db to extract SDC Timelines...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # We order by start_time ASC so that newer sessions overwrite values for the same (PAN, Filing Period)
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
        
        session_pan = row['pan'] or ""
        session_full_name = ""
        session_temp_name = ""
        session_form = ""
        session_ay = ""
        session_status_raw = ""
        session_due_date = ""
        
        for step in timeline:
            cap = step.get('captured_data')
            if cap:
                if cap.get('pan'):
                    session_pan = cap.get('pan')
                if cap.get('client_name') and cap.get('client_name') != cap.get('client_temp_name'):
                    session_full_name = cap.get('client_name')
                if cap.get('client_temp_name'):
                    session_temp_name = cap.get('client_temp_name')
                if cap.get('form'):
                    session_form = cap.get('form')
                if cap.get('ay'):
                    session_ay = cap.get('ay')
                if cap.get('status'):
                    session_status_raw = cap.get('status')
                if cap.get('due_date'):
                    session_due_date = cap.get('due_date')
                    
        final_name = session_full_name if session_full_name else session_temp_name
        
        if not session_pan:
            continue
            
        filing_period = session_ay if session_ay else "Unknown Period"
        
        key = (session_pan, filing_period)
        submit_status = evaluate_status(session_status_raw)
        site_history = f"Start: {start_url} ({start_time})\nEnd: {end_url} ({end_time})\nSteps: {len(timeline)}"
        
        if key not in ltt_dict:
            ltt_dict[key] = {
                "PAN": session_pan,
                "Client Name": final_name,
                "Filing Period": filing_period,
                "Filing Type": session_form,
                "Submit Status": submit_status,
                "Due Date": session_due_date,
                "Session ID": session_id,
                "Site History": site_history,
                "Last Updated": end_time if end_time else start_time
            }
        else:
            existing = ltt_dict[key]
            existing["Filing Type"] = session_form if session_form else existing["Filing Type"]
            existing["Submit Status"] = submit_status if session_status_raw else existing["Submit Status"]
            if session_due_date:
                existing["Due Date"] = session_due_date
            existing["Session ID"] = session_id
            existing["Site History"] = site_history
            existing["Last Updated"] = end_time if end_time else start_time
            
            if session_full_name and (not existing["Client Name"] or existing["Client Name"] == session_temp_name):
                existing["Client Name"] = session_full_name

    return list(ltt_dict.values())

def generate_ltt_excel():
    data = process_timelines()
    if not data:
        print("No SDC timeline data found or extracted.")
        return
        
    df = pd.DataFrame(data)
    
    cols = ["PAN", "Client Name", "Filing Period", "Filing Type", "Submit Status", "Due Date", "Session ID", "Last Updated", "Site History"]
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
    
    target_out = output_file
    try:
        writer = pd.ExcelWriter(target_out, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Live Tracking Table (LTT)')
        worksheet = writer.sheets['Live Tracking Table (LTT)']
        worksheet.column_dimensions['A'].width = 15 
        worksheet.column_dimensions['B'].width = 30 
        worksheet.column_dimensions['C'].width = 15 
        worksheet.column_dimensions['D'].width = 15 
        worksheet.column_dimensions['E'].width = 40 
        worksheet.column_dimensions['F'].width = 25 
        worksheet.column_dimensions['G'].width = 25 
        worksheet.column_dimensions['H'].width = 80 
        writer.close()
    except PermissionError:
        target_out = os.path.join(live_dir, f"Live_Tracking_Table_LTT_{datetime.now().strftime('%H%M%S')}.xlsx")
        writer = pd.ExcelWriter(target_out, engine='openpyxl')
        df.to_excel(writer, index=False, sheet_name='Live Tracking Table (LTT)')
        worksheet = writer.sheets['Live Tracking Table (LTT)']
        worksheet.column_dimensions['A'].width = 15 
        worksheet.column_dimensions['B'].width = 30 
        worksheet.column_dimensions['C'].width = 15 
        worksheet.column_dimensions['D'].width = 15 
        worksheet.column_dimensions['E'].width = 40 
        worksheet.column_dimensions['F'].width = 25 
        worksheet.column_dimensions['G'].width = 25 
        worksheet.column_dimensions['H'].width = 80 
        writer.close()

    print(f"Success! Generated {target_out} with {len(data)} records.")
    return target_out

if __name__ == '__main__':
    print("--- SDC Parser (Live Tracking Table) ---")
    generate_ltt_excel()
