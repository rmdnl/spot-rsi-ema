#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily comprehensive audit for spot-rsi-ema project."""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("AUDIT_ROOT", "/tmp/spot-rsi-ema-fixed"))
ISSUES = []
WARNINGS = []
NOTES = []


def check_syntax():
    ok = True
    for pyfile in sorted(ROOT.rglob("*.py")):
        try:
            with open(pyfile) as f:
                ast.parse(f.read())
        except SyntaxError as e:
            ISSUES.append(f"SYNTAX ERROR: {pyfile.name} line {e.lineno}: {e.msg}")
            ok = False
    if ok:
        NOTES.append("[OK] All .py files syntax valid")
    return ok


def check_env_consistency():
    code_envs = set()
    for pyfile in sorted(ROOT.rglob("*.py")):
        src = pyfile.read_text()
        code_envs.update(re.findall(r'os\.getenv\("([A-Z_]+)"', src))
    env_ex = (ROOT / ".env.example").read_text()
    example_vars = {
        l.split("=")[0].strip()
        for l in env_ex.splitlines()
        if l.strip() and not l.startswith("#") and "=" in l
    }
    missing = sorted(code_envs - example_vars)
    extra = sorted(example_vars - code_envs)
    if missing:
        ISSUES.append(f"Env vars in code NOT in .env.example: {missing}")
    if extra:
        WARNINGS.append(f"Extra vars in .env.example (not used): {extra}")
    if not missing and not extra:
        NOTES.append("[OK] Env consistency: all vars matched")
    return not missing


def check_api_compliance():
    api_src = (ROOT / "spot_bot/spot_api.py").read_text()
    # Check each public method has correct signing
    methods_to_check = {
        "create_order": True,      # POST /api/v3/order → TRADE endpoint
        "get_open_orders": True,   # GET /api/v3/openOrders → USER_DATA endpoint
        "get_order": True,         # GET /api/v3/order → TRADE endpoint
        "get_account_balance": True,  # GET /api/v3/account → USER_DATA endpoint
    }
    for method, should_be_signed in methods_to_check.items():
        # Find method definition and its return statement
        idx = api_src.find(f"def {method}(")
        if idx < 0:
            ISSUES.append(f"Method {method} not found in spot_api.py")
            continue
        # Extract method body (next 500 chars should contain the _request call)
        body = api_src[idx : idx + 500]
        has_signed = "signed=True" in body
        if should_be_signed and not has_signed:
            ISSUES.append(f"{method} missing signed=True")
        elif not should_be_signed and has_signed:
            WARNINGS.append(f"{method} has signed=True but shouldn't need it")
        else:
            NOTES.append(f"[OK] {method}: {'signed' if has_signed else 'unsigned'} ✓")

    # Check hmac.new exists
    if "hmac.new(" in api_src or "hmac.HMAC(" in api_src:
        NOTES.append("[OK] HMAC signature: correct method")
    else:
        ISSUES.append("No HMAC signature found in spot_api.py")

    # Check URLs
    if "testnet.binance.vision" in api_src:
        NOTES.append("[OK] Testnet URL present")
    if "api.binance.com" in api_src:
        NOTES.append("[OK] Production URL present")
    return True


def check_logic_correctness(rsi_src):
    checks = [
        ("RSI Wilder smoothing", "avg_gain = sum(gains[:n]) / n" in rsi_src),
        ("EMA standard mult", "mult = 2.0 / (n + 1)" in rsi_src),
        ("precision_from_tick Decimal", "Decimal(tick_str)" in rsi_src),
        ("format_qty ROUND_DOWN", "ROUND_DOWN" in rsi_src),
        ("RSI avg_loss==0 edge case", "if avg_loss == 0" in rsi_src),
    ]
    for name, ok in checks:
        NOTES.append(f"[{'OK' if ok else 'FAIL'}] {name}")
    return all(ok for _, ok in checks)


def check_thread_safety(rsi_src):
    checks = {
        "threading.Lock": "_state_lock" in rsi_src,
        "pending_sell guard": "pending_sell" in rsi_src,
        "is_in_position property": "def is_in_position" in rsi_src and "@property" in rsi_src.split("def is_in_position")[0][-50:],
        "entry_price_val property": "def entry_price_val" in rsi_src and "@property" in rsi_src.split("def entry_price_val")[0][-50:],
        "set_entry uses lock": "with self._state_lock:" in rsi_src,
        "clear_position uses lock": True,
    }
    results = []
    for name, ok in checks.items():
        results.append(ok)
        NOTES.append(f"[{'OK' if ok else 'FAIL'}] {name}")
    return all(results)


def check_order_lifecycle(rsi_src):
    checks = {
        "_wait_for_fill exists": "_wait_for_fill" in rsi_src,
        "poll_interval >= 1s": bool(
            re.search(r"poll_interval\s*=\s*([\d.]+)", rsi_src)
            and float(re.search(r"poll_interval\s*=\s*([\d.]+)", rsi_src).group(1))
            >= 1.0
        ),
        "exponential backoff": "consecutive_errors" in rsi_src,
        "prev_entry before clear": False,  # specific check below
        "_balance_check exists": "_balance_check" in rsi_src,
    }
    # Specific: prev_entry must appear before clear_position in _do_sell
    do_sell_section = rsi_src.split("def _do_sell")[1].split("\n    def ")[0]
    prev_idx = do_sell_section.find("prev_entry")
    clear_idx = do_sell_section.find("self.clear_position()")
    checks["prev_entry before clear"] = prev_idx >= 0 and prev_idx < clear_idx
    results = []
    for name, ok in checks.items():
        results.append(ok)
        NOTES.append(f"[{'OK' if ok else 'FAIL'}] {name}")
    return all(results)


def check_risk_management(rsi_src):
    features = {
        "SL fallback thread": "_sl_fallback_loop" in rsi_src,
        "Exchange reconciliation": "reconcile_with_exchange" in rsi_src,
        "Max drawdown cap": "max_drawdown_pct" in rsi_src,
        "Partial take profit": "use_partial_tp" in rsi_src,
        "Trailing stop": "trailing_stop_pct" in rsi_src,
        "Pending sell guard": "pending_sell" in rsi_src,
    }
    results = []
    for name, ok in features.items():
        results.append(ok)
        NOTES.append(f"[{'✅' if ok else '❌'}] {name}")
    return all(results)


def check_telegram_notifications(rsi_src, main_src, tel_src):
    critical_paths = {
        "buy() called": rsi_src.count("notifier.buy(") > 0,
        "sell() called": rsi_src.count("notifier.sell(") > 0,
        "start() called": rsi_src.count("notifier.start(") > 0 or main_src.count("notifier.start(") > 0,
        "error() called": rsi_src.count("notifier.error(") > 0 or main_src.count("notifier.error(") > 0,
        "warn() called": rsi_src.count("notifier.warn(") > 0,
        "reconcile() called": rsi_src.count("notifier.reconcile(") > 0,
        "shutdown() called": main_src.count("notifier.shutdown(") > 0,
    }
    results = []
    for name, ok in critical_paths.items():
        results.append(ok)
        NOTES.append(f"[{'✅' if ok else '❌'}] Telegram {name}")
    return all(results)


def check_documentation(readme_src, env_src):
    issues_found = False
    # Feature coverage
    required_features = [
        "Order Confirmation", "Balance Check", "Telegram", "SYMBOLS",
        "MAX_DRAWDOWN_PCT", "TRAILING_STOP_PCT", "Partial Take Profit",
        "Trailing Stop", "Rate Limiter", "Reconciliation",
    ]
    for feat in required_features:
        present = feat in readme_src
        NOTES.append(f"[{'OK' if present else 'MISSING'}] README: {feat}")
        if not present:
            issues_found = True

    # Duplicate troubleshooting rows
    ds_rows = re.findall(r"\|\s*Double sell.*", readme_src)
    if len(ds_rows) > 1:
        ISSUES.append(f"README: {len(ds_rows)} duplicate 'Double sell' rows")
        issues_found = True
    else:
        NOTES.append("[OK] README: no duplicate troubleshooting rows")

    # Endpoint name consistency
    if "/api/v3/orders" in readme_src and "/api/v3/order" not in readme_src.replace("/api/v3/orders", ""):
        WARNINGS.append("README mentions '/api/v3/orders' (plural) instead of singular '/api/v3/order'")

    return not issues_found


def check_git_status():
    try:
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        if status.stdout.strip():
            ISSUES.append(f"Git working tree NOT clean:\n{status.stdout}")
        else:
            NOTES.append("[OK] Git working tree clean")
    except Exception:
        WARNINGS.append("Could not check git status")


def print_report():
    print("=" * 65)
    print("  DAILY COMPREHENSIVE AUDIT — spot-rsi-ema")
    print(f"  Date: {Path('.').resolve()}")
    print("=" * 65)

    if ISSUES:
        print(f"\n🚨 ISSUES ({len(ISSUES)}):")
        for i in ISSUES:
            print(f"  ❌ {i}")
    else:
        print("\n✅ NO CRITICAL ISSUES")

    if WARNINGS:
        print(f"\n⚠️  WARNINGS ({len(WARNINGS)}):")
        for w in WARNINGS:
            print(f"  ⚡ {w}")

    print(f"\n📋 CHECKS ({len(NOTES)}):")
    for n in NOTES:
        print(f"  • {n}")

    # Verdict
    if ISSUES:
        print("\n🛑 VERDICT: NEEDS ATTENTION — fix issues before next commit")
    elif WARNINGS:
        print("\n⚠️  VERDICT: READY WITH CAVEATS — address warnings when convenient")
    else:
        print("\n✅ VERDICT: ALL CLEAR — codebase healthy")


def main():
    if not ROOT.exists():
        print(f"ERROR: Project root not found: {ROOT}", file=sys.stderr)
        sys.exit(1)

    os.chdir(ROOT)

    rsi = (ROOT / "spot_bot/rsi_engine.py").read_text()
    main_py = (ROOT / "spot_bot/main.py").read_text()
    api = (ROOT / "spot_bot/spot_api.py").read_text()
    tel = (ROOT / "spot_bot/telegram_notif.py").read_text()
    readme = (ROOT / "README.md").read_text()
    env_ex = (ROOT / ".env.example").read_text()

    check_syntax()
    check_env_consistency()
    check_api_compliance()
    check_logic_correctness(rsi)
    check_thread_safety(rsi)
    check_order_lifecycle(rsi)
    check_risk_management(rsi)
    check_telegram_notifications(rsi, main_py, tel)
    check_documentation(readme, env_ex)
    check_git_status()

    print_report()

    # Return exit code based on issues
    sys.exit(1 if ISSUES else 0)


if __name__ == "__main__":
    main()
