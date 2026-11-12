# -*- coding: utf-8 -*-
"""
Background scheduler for daily spot-rsi-ema audit.
Runs every hour, checks if today's audit hasn't been done yet.
Start once, runs continuously in background.
Usage: python scripts/daemon_audit.py
"""
import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(r"C:\tmp\spot-rsi-ema-fixed")
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "daily_audit.py"
LOG_DIR = PROJECT_ROOT / "scripts" / "logs"
CHECK_INTERVAL_MINUTES = 60  # Check every hour
AUDIT_HOUR = 9               # Target hour for daily audit

LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_last_audit_date():
    """Check if today's audit has already run."""
    today = datetime.now().strftime("%Y%m%d")
    for f in LOG_DIR.glob(f"audit_{today}_*.txt"):
        return True
    return False


def run_audit():
    """Execute the audit script and save output to log file."""
    log_file = LOG_DIR / f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n--- STDERR ---\n")
                f.write(result.stderr)
        print(f"[{datetime.now()}] Audit completed → {log_file.name}")
        return result.returncode == 0
    except Exception as e:
        with open(log_file, "w") as f:
            f.write(f"Audit FAILED: {e}\n")
        print(f"[{datetime.now()}] Audit failed: {e}")
        return False


def should_run_today():
    """Check if we should run the daily audit now."""
    now = datetime.now()
    # Run at target hour OR if not run yet today
    past_target_hour = now.hour >= AUDIT_HOUR
    already_run = get_last_audit_date()
    return past_target_hour and not already_run


def main():
    print(f"🕐 Audit daemon started. Checking every {CHECK_INTERVAL_MINUTES}min.")
    print(f"   Target: daily at {AUDIT_HOUR}:00\n")

    while True:
        try:
            if should_run_today():
                print(f"[{datetime.now()}] Running daily audit...")
                run_audit()
            else:
                minutes_left = (
                    AUDIT_HOUR - datetime.now().hour
                ) * 60 - datetime.now().minute
                if minutes_left > 0:
                    print(f"[{datetime.now()}] Next audit in ~{minutes_left}min (target {AUDIT_HOUR}:00)")
        except KeyboardInterrupt:
            print("\n[STOP] Daemon stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)

        time.sleep(CHECK_INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
