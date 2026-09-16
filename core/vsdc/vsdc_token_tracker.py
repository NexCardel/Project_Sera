"""
core/vsdc/vsdc_token_tracker.py - Gemini Flash Token and Daily Quota Tracker
============================================================================
Tracks daily API calls, prompt tokens, candidate tokens, and total token usage.
Persists counters to a local JSON file in data/gemini_token_stats.json.
Provides helper methods for the Tracker Dump UI usage meter.
"""

import os
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

def get_stats_file() -> Path:
    """Returns a guaranteed writable path for gemini_token_stats.json in the user profile."""
    stats_dir = Path.home() / "AmanAssociates_Sera" / "data"
    try:
        stats_dir.mkdir(parents=True, exist_ok=True)
        return stats_dir / "gemini_token_stats.json"
    except Exception:
        import tempfile
        t_dir = Path(tempfile.gettempdir()) / "AmanAssociates_Sera" / "data"
        t_dir.mkdir(parents=True, exist_ok=True)
        return t_dir / "gemini_token_stats.json"


STATS_FILE = get_stats_file()
FREE_TIER_DAILY_LIMIT = 1500  # Google AI Studio Free Tier RPD limit

_lock = threading.Lock()


def _get_today_str() -> str:
    """Returns current date string in YYYY-MM-DD local format."""
    return datetime.now().strftime("%Y-%m-%d")


def _load_stats() -> Dict[str, Any]:
    """Loads stats from disk or initializes defaults."""
    if STATS_FILE.exists():
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"[TokenTracker] Warning loading stats: {e}")
    
    return {
        "dates": {},
        "lifetime_calls": 0,
        "lifetime_prompt_tokens": 0,
        "lifetime_candidate_tokens": 0,
        "lifetime_total_tokens": 0,
        "last_updated": ""
    }


def _save_stats(data: Dict[str, Any]):
    """Saves stats safely to disk."""
    try:
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_file = STATS_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(STATS_FILE)
    except Exception as e:
        print(f"[TokenTracker] Warning saving stats: {e}")


def record_gemini_call(
    prompt_tokens: int,
    candidate_tokens: int,
    total_tokens: int,
    model: str = "gemini-3.6-flash",
    success: bool = True
):
    """
    Records token metrics for a completed Gemini API call.
    """
    with _lock:
        data = _load_stats()
        today = _get_today_str()
        
        if today not in data["dates"]:
            data["dates"][today] = {
                "calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "prompt_tokens": 0,
                "candidate_tokens": 0,
                "total_tokens": 0,
                "models": {}
            }
        
        day_entry = data["dates"][today]
        day_entry["calls"] += 1
        if success:
            day_entry["successful_calls"] += 1
        else:
            day_entry["failed_calls"] += 1
            
        day_entry["prompt_tokens"] += max(0, prompt_tokens)
        day_entry["candidate_tokens"] += max(0, candidate_tokens)
        day_entry["total_tokens"] += max(0, total_tokens)
        
        if model not in day_entry["models"]:
            day_entry["models"][model] = 0
        day_entry["models"][model] += 1
        
        # Update lifetime stats
        data["lifetime_calls"] = data.get("lifetime_calls", 0) + 1
        data["lifetime_prompt_tokens"] = data.get("lifetime_prompt_tokens", 0) + max(0, prompt_tokens)
        data["lifetime_candidate_tokens"] = data.get("lifetime_candidate_tokens", 0) + max(0, candidate_tokens)
        data["lifetime_total_tokens"] = data.get("lifetime_total_tokens", 0) + max(0, total_tokens)
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        
        _save_stats(data)


def get_token_usage_summary() -> Dict[str, Any]:
    """
    Returns today's token and quota usage metrics.
    """
    with _lock:
        data = _load_stats()
        today = _get_today_str()
        day_entry = data["dates"].get(today, {
            "calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "total_tokens": 0,
            "models": {}
        })
        
        calls_today = day_entry["calls"]
        calls_remaining = max(0, FREE_TIER_DAILY_LIMIT - calls_today)
        
        return {
            "date": today,
            "calls_today": calls_today,
            "calls_remaining": calls_remaining,
            "daily_limit": FREE_TIER_DAILY_LIMIT,
            "prompt_tokens_today": day_entry["prompt_tokens"],
            "candidate_tokens_today": day_entry["candidate_tokens"],
            "total_tokens_today": day_entry["total_tokens"],
            "lifetime_calls": data.get("lifetime_calls", 0),
            "lifetime_tokens": data.get("lifetime_total_tokens", 0),
            "badge_text": f"Gemini Flash: {calls_today} Calls ({day_entry['total_tokens']:,} Tks) | {calls_remaining:,} Free Left",
            "tooltip": (
                f"<b>Gemini Flash Free Quota Usage (Today: {today})</b><br>"
                f"Calls: <b>{calls_today}</b> / {FREE_TIER_DAILY_LIMIT} ({calls_remaining} remaining)<br>"
                f"Prompt Tokens: <b>{day_entry['prompt_tokens']:,}</b><br>"
                f"Generated Tokens: <b>{day_entry['candidate_tokens']:,}</b><br>"
                f"Total Tokens: <b>{day_entry['total_tokens']:,}</b><br>"
                f"Lifetime Calls: <b>{data.get('lifetime_calls', 0):,}</b> ({data.get('lifetime_total_tokens', 0):,} tokens)<br>"
                f"Estimated Cost: <b>$0.00</b> (100% Free Tier)"
            )
        }


def reset_token_metrics():
    """Resets today's and lifetime token metrics."""
    with _lock:
        data = {
            "dates": {},
            "lifetime_calls": 0,
            "lifetime_prompt_tokens": 0,
            "lifetime_candidate_tokens": 0,
            "lifetime_total_tokens": 0,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
        _save_stats(data)

