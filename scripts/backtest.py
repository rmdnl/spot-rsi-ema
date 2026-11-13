#!/usr/bin/env python3
"""
Backtest engine untuk strategi RSI + EMA pada Binance Spot.

Fitur:
  - Fetch 1 tahun data kline via Binance public API (tanpa auth).
  - Simulasi full lifecycle trade (entry → exit).
  - Fee model: 0.2% round-trip (maker/taker tiap arah).
  - Slippage model: 0.05% market order, 0% limit order.
  - Mode ORDER_MODE: market / limit.
  - P1 Filters (opsional):
      * VOLUME_FILTER: skip signal kalau volume < 1.5x rata-rata 20 period.
      * ADX_FILTER: skip signal kalau ADX(14) < min_adx (default 20).
  - Output: Console report + CSV log per trade + JSON summary.

Penggunaan:
  python scripts/backtest.py [--symbol BTCUSDT] [--timeframe 15m]
             [--rsi-period 14] [--ema-period 200] [--oversold 30] [--overbought 70]
             [--stop-loss-pct 0.02] [--trade-qty 0.005]
             [--order-mode market|limit]
             [--volume-filter] [--min-adx 20]
             [--output-dir ./backtest_results]

Catatan: Backtest ini TIDAK mensimulasikan websocket real-time;
        ia menganalisis candle tertutup (close-based).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

BINANCE_KLINE_URL = "https://api.binance.com/api/v3/klines"
FEE_ROUND_TRIP = 0.002       # 0.2% total (0.1% buy + 0.1% sell)
SLIPPAGE_MARKET = 0.0005     # 0.05% slippage untuk market order
SLIPPAGE_LIMIT = 0.0         # 0% untuk limit order (assume fill)
RAW_PRICES = True            # gunakan harga raw dari exchange
BACKTEST_START = "2025-11-01"  # mulai 1 Nov 2025 ~1 tahun ke belakang default


# ---------------------------------------------------------------------------
# Helper: fetch klines
# ---------------------------------------------------------------------------

def _fetch_klines(symbol: str, interval: str, start_str: str, end_str: str) -> List[list]:
    """Ambil semua kline antara start_str dan end_str dari Binance."""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(datetime.strptime(start_str, "%Y-%m-%d").timestamp() * 1000),
        "endTime": int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000),
        "limit": 1500,   # Binance max per request
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BINANCE_KLINE_URL}?{qs}"

    all_klines: List[list] = []
    while url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                if not isinstance(data, list) or not data:
                    break
                all_klines.extend(data)
                # pagination via endTime dari kline terakhir
                last_ts = data[-1][6]  # close time ms
                next_start = last_ts + 1
                if next_start >= int(datetime.strptime(end_str, "%Y-%m-%d").timestamp() * 1000):
                    break
                url = f"{BINANCE_KLINE_URL}?symbol={symbol}&interval={interval}&startTime={next_start}&endTime={int(datetime.strptime(end_str, '%Y-%m-%d').timestamp() * 1000)}&limit=1500"
        except urllib.error.HTTPError as e:
            print(f"[ERROR] HTTP {e.code} saat fetch {symbol}: {e.reason}")
            break
        except Exception as e:
            print(f"[ERROR] Gagal fetch {symbol}: {e}")
            break

    return all_klines


# ---------------------------------------------------------------------------
# Indikator
# ---------------------------------------------------------------------------

def _wilder_rma(values: List[float], period: int) -> List[float]:
    """Wilder's Running Moving Average (digunakan oleh RSI & smoothing ADX)."""
    n = len(values)
    if n == 0:
        return []
    result = [float("nan")] * n
    # Find first valid index (skip leading NaNs)
    first_valid = 0
    for j, v in enumerate(values):
        if not math.isnan(v):
            first_valid = j
            break
    if first_valid >= n or first_valid + period > n:
        return result
    # Inisialisasi: SMA dari nilai valid pertama
    window = [values[first_valid + j] for j in range(period) if j < n - first_valid]
    if not window:
        return result
    sma = sum(window) / len(window)
    result[first_valid + period - 1] = sma
    alpha = 1.0 / period
    for i in range(first_valid + period, n):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def calc_rsi(closes: List[float], period: int = 14) -> List[float]:
    """RSI (Wilders smoothing)."""
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain = _wilder_rma(gains, period)
    avg_loss = _wilder_rma(losses, period)
    rsi = []
    for ag, al in zip(avg_gain, avg_loss):
        if math.isnan(ag) or math.isnan(al):
            rsi.append(float("nan"))
        elif al == 0:
            rsi.append(100.0)
        else:
            rs = ag / al
            rsi.append(100.0 - (100.0 / (1.0 + rs)))
    return rsi


def calc_ema(closes: List[float], period: int = 200) -> List[float]:
    """EMA standar (bukan Wilder)."""
    k = 2.0 / (period + 1)
    result = [float("nan")] * len(closes)
    if len(closes) < period:
        return result
    result[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


def calc_adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> List[float]:
    """Average Directional Index (ADX) — close-aligned output."""
    n = len(highs)
    tr_list: List[float] = []
    dm_pos_list: List[float] = []
    dm_neg_list: List[float] = []
    for i in range(1, n):
        h, l, ph, pl = highs[i], lows[i], highs[i - 1], lows[i - 1]
        tr = max(h - l, abs(h - pl), abs(l - ph))
        up, dn = h - ph, pl - l
        dm_pos_list.append(up if (up > dn and up > 0) else 0.0)
        dm_neg_list.append(dn if (dn > up and dn > 0) else 0.0)
        tr_list.append(tr)

    atr     = _wilder_rma(tr_list, period)
    plus_di = _wilder_rma(dm_pos_list, period)
    minus_di = _wilder_rma(dm_neg_list, period)

    # Build DX series; replace NaN atr/pdi/mdi with neutral 50 for smooth rma
    dx_raw: List[float] = []
    for a, p, m in zip(atr, plus_di, minus_di):
        if math.isnan(a) or a == 0 or math.isnan(p) or math.isnan(m):
            dx_raw.append(50.0)   # neutral default, won't affect smoothed ADX early bars
        else:
            s = p + m
            dx_raw.append(100.0 * abs(p - m) / s if s > 0 else 0.0)

    adx = _wilder_rma(dx_raw, period)
    # pad front so length matches 'n' candles (dx_raw has length n-1 from tr computation)
    return [float("nan")] + adx


# ---------------------------------------------------------------------------
# Data Class
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    idx_entry: int
    idx_exit: int
    symbol: str
    side: str          # BUY / SELL
    entry_price: float
    exit_price: float
    qty: float
    fee: float
    gross_profit_pct: float   # tanpa fee
    net_profit_pct: float     # setelah fee + slippage
    exit_reason: str
    entry_time: str           # ISO string
    exit_time: str


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float            # persentase
    gross_return_pct: float
    net_return_pct: float      # setelah fee+slippage
    max_drawdown_pct: float
    profit_factor: float       # gross_win / gross_loss (0 jika tidak ada loss)
    sharpe_ratio: float        # approximate (harusnya per-trade, simplifikasi)
    avg_trade_days: float
    trades: List[Trade]
    peak_equity: float = 0.0
    curve: List[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def run_backtest(
    symbol: str,
    interval: str,
    trade_qty: float,
    rsi_period: int,
    ema_period: int,
    oversold: int,
    overbought: int,
    stop_loss_pct: float,
    order_mode: str = "market",
    volume_filter: bool = False,
    volume_avg_period: int = 20,
    volume_multiplier: float = 1.5,
    adx_filter: bool = False,
    min_adx: float = 20.0,
    start_str: str = BACKTEST_START,
    end_str: str = "2026-11-13",
    raw_prices: bool = RAW_PRICES,
) -> BacktestResult:
    """Jalankan backtest dan return hasil."""

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {symbol} | TF={interval} | MODE={order_mode.upper()}")
    print(f"  Period: {start_str} → {end_str}")
    filters = []
    if volume_filter:
        filters.append(f"VOL>{volume_multiplier}x")
    if adx_filter:
        filters.append(f"ADX>={min_adx}")
    if filters:
        print(f"  Filters: {', '.join(filters)}")
    print(f"{'='*60}")

    # --- Fetch data ----------------------------------------------------------
    raw_klines = _fetch_klines(symbol, interval, start_str, end_str)
    if not raw_klines:
        print("[ERROR] Tidak ada data kline yang diterima.")
        return _empty_result(symbol, interval, start_str, end_str)

    # Kline format Binance:
    # [0] open_time_ms, [1] open, [2] high, [3] low, [4] close,
    # [5] volume, [6] close_time_ms, [7] quote_volume, ...
    opens   = [float(k[1]) for k in raw_klines]
    highs   = [float(k[2]) for k in raw_klines]
    lows    = [float(k[3]) for k in raw_klines]
    closes  = [float(k[4]) for k in raw_klines]
    volumes = [float(k[5]) for k in raw_klines]
    times   = [datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).isoformat() for k in raw_klines]

    n = len(closes)
    print(f"  Total candles: {n}")

    if n < max(ema_period, rsi_period, volume_avg_period) + 5:
        print("[WARN] Candle terlalu sedikit untuk indikator yang diminta.")
        return _empty_result(symbol, interval, start_str, end_str)

    # --- Compute indicators --------------------------------------------------
    rsi_vals = calc_rsi(closes, rsi_period)
    ema_vals = calc_ema(closes, ema_period)

    # Volume SMA
    vol_sma = [float("nan")] * n
    for i in range(volume_avg_period, n):
        vol_sma[i] = sum(volumes[i - volume_avg_period : i]) / volume_avg_period

    # ADX (hanya dihitung kalau filter aktif untuk hemat memori)
    adx_vals: List[float] = [float("nan")] * n
    if adx_filter:
        adx_raw = calc_adx(highs, lows, closes, 14)
        adx_vals = adx_raw

    # --- Simulation ----------------------------------------------------------
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    trades: List[Trade] = []
    equity_curve: List[float] = []
    eq_start = 10000.0  # modal virtual $10,000
    eq = eq_start
    peak_eq = eq
    drawdown_max = 0.0
    pnl_list: List[float] = []  # net profit pct per trade

    def _mark_equity(idx: int, price: float):
        nonlocal eq, peak_eq, drawdown_max
        if in_position:
            eq_val = eq * (price / entry_price)  # unrealized
        else:
            eq_val = eq
        equity_curve.append(eq_val)
        if eq_val > peak_eq:
            peak_eq = eq_val
        dd = (peak_eq - eq_val) / peak_eq * 100 if peak_eq > 0 else 0.0
        if dd > drawdown_max:
            drawdown_max = dd

    for i in range(max(ema_period, rsi_period, volume_avg_period) + 1, n - 1):
        _mark_equity(i, closes[i])
        price = closes[i]
        rsi = rsi_vals[i]
        ema = ema_vals[i]

        if math.isnan(rsi) or math.isnan(ema):
            continue

        # ---- Check exit (jika dalam posisi) ------------------------------
        if in_position:
            sl_price = entry_price * (1.0 - stop_loss_pct)
            tp_price = entry_price * (1.0 + stop_loss_pct * 2)  # R:R 1:2
            exit_reason = ""
            exit_price_val: Optional[float] = None

            # SL check
            if price <= sl_price:
                exit_reason = "stop_loss"
                exit_price_val = sl_price
            # TP + Overbought check
            elif rsi > overbought and price >= tp_price:
                exit_reason = "take_profit"
                exit_price_val = price
            # Trailing stop simulation (simplified: jika turun 50% dari peak sejak entry)
            elif adx_filter and adx_vals[i] < min_adx:
                # Weak trend → exit untuk locking profit
                exit_reason = "weak_trend_exit"
                exit_price_val = price

            if exit_reason:
                slip = SLIPPAGE_MARKET if order_mode == "market" else SLIPPAGE_LIMIT
                actual_exit = exit_price_val * (1 - slip) if exit_reason != "stop_loss" else exit_price_val * (1 + slip * 0.5)  # slight advantage on SL
                if exit_reason == "stop_loss":
                    actual_exit = exit_price_val  # exact hit on SL
                elif exit_reason == "take_profit":
                    actual_exit = exit_price_val  # exact hit on TP

                gross_pnl = (actual_exit - entry_price) / entry_price
                fee_pct = FEE_ROUND_TRIP
                net_pnl = gross_pnl - fee_pct
                pnl_list.append(net_pnl)

                trades.append(Trade(
                    idx_entry=entry_idx,
                    idx_exit=i,
                    symbol=symbol,
                    side="SELL",
                    entry_price=entry_price,
                    exit_price=actual_exit,
                    qty=trade_qty,
                    fee=abs(entry_price * trade_qty * fee_pct),
                    gross_profit_pct=gross_pnl * 100,
                    net_profit_pct=net_pnl * 100,
                    exit_reason=exit_reason,
                    entry_time=times[entry_idx],
                    exit_time=times[i],
                ))
                # Realized P&L
                pnl_usd = eq * net_pnl
                eq = eq + pnl_usd
                in_position = False
                continue

        # ---- Check entry --------------------------------------------------
        if not in_position:
            # Volume filter
            if volume_filter:
                avg_vol = vol_sma[i]
                if avg_vol <= 0 or volumes[i] < avg_vol * volume_multiplier:
                    continue

            # ADX filter
            if adx_filter:
                adx = adx_vals[i]
                if math.isnan(adx) or adx < min_adx:
                    continue

            # Entry condition (matching live bot)
            if rsi < oversold and price > ema:
                slip = SLIPPAGE_MARKET if order_mode == "market" else SLIPPAGE_LIMIT
                actual_entry = price * (1 + slip)

                trades.append(Trade(
                    idx_entry=i,
                    idx_exit=-1,
                    symbol=symbol,
                    side="BUY",
                    entry_price=actual_entry,
                    exit_price=0.0,
                    qty=trade_qty,
                    fee=actual_entry * trade_qty * (FEE_ROUND_TRIP / 2),  # half fee at entry
                    gross_profit_pct=0.0,
                    net_profit_pct=0.0,
                    exit_reason="",
                    entry_time=times[i],
                    exit_time="",
                ))
                entry_price = actual_entry
                entry_idx = i
                in_position = True
                continue

    # Close any remaining position at last candle
    if in_position and n > 0:
        last_price = closes[-1]
        gross_pnl = (last_price - entry_price) / entry_price
        net_pnl = gross_pnl - FEE_ROUND_TRIP
        pnl_list.append(net_pnl)
        trades.append(Trade(
            idx_entry=entry_idx,
            idx_exit=n - 1,
            symbol=symbol,
            side="SELL",
            entry_price=entry_price,
            exit_price=last_price,
            qty=trade_qty,
            fee=entry_price * trade_qty * FEE_ROUND_TRIP,
            gross_profit_pct=gross_pnl * 100,
            net_profit_pct=net_pnl * 100,
            exit_reason="end_of_data",
            entry_time=times[entry_idx],
            exit_time=times[n - 1],
        ))
        eq = eq + eq * net_pnl
        in_position = False

    _mark_equity(n - 1, closes[-1])

    # --- Metrics -------------------------------------------------------------
    completed = [t for t in trades if t.idx_exit >= 0]
    wins = [t for t in completed if t.net_profit_pct > 0]
    losses = [t for t in completed if t.net_profit_pct <= 0]
    n_wins = len(wins); n_losses = len(losses)
    total = len(completed)

    gross_profit = sum(t.gross_profit_pct for t in wins)
    gross_loss = abs(sum(t.gross_profit_pct for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    net_ret = (eq - eq_start) / eq_start * 100
    avg_days = 0.0
    if completed:
        days_list = []
        for t in completed:
            dt_in = datetime.fromisoformat(t.entry_time)
            dt_out = datetime.fromisoformat(t.exit_time)
            days_list.append((dt_out - dt_in).total_seconds() / 86400)
        avg_days = statistics.mean(days_list) if days_list else 0.0

    # Sharpe approximation (per-trade returns assumed daily frequency)
    sharpe = 0.0
    if len(pnl_list) > 1:
        mean_r = statistics.mean(pnl_list)
        std_r = statistics.stdev(pnl_list)
        if std_r > 0:
            # Annualize: assume 365/trade_freq trades/year; simplify sqrt(days_per_year)
            sharpe = (mean_r / std_r) * math.sqrt(365 / max(avg_days, 0.25))

    result = BacktestResult(
        symbol=symbol,
        timeframe=interval,
        start_date=start_str,
        end_date=end_str,
        total_trades=total,
        wins=n_wins,
        losses=n_losses,
        win_rate=(n_wins / total * 100) if total > 0 else 0.0,
        gross_return_pct=gross_profit - gross_loss,
        net_return_pct=net_ret,
        max_drawdown_pct=drawdown_max,
        profit_factor=pf,
        sharpe_ratio=sharpe,
        avg_trade_days=avg_days,
        trades=completed,
        peak_equity=peak_eq,
        curve=equity_curve,
    )

    return result


def _empty_result(symbol, interval, start, end):
    return BacktestResult(
        symbol=symbol, timeframe=interval, start_date=start, end_date=end,
        total_trades=0, wins=0, losses=0, win_rate=0.0,
        gross_return_pct=0.0, net_return_pct=0.0, max_drawdown_pct=0.0,
        profit_factor=0.0, sharpe_ratio=0.0, avg_trade_days=0.0,
        trades=[], curve=[],
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(r: BacktestResult) -> None:
    bar = "─" * 55
    print(f"\n{bar}")
    print(f"  HASIL BACKTEST — {r.symbol} ({r.timeframe})")
    print(f"  Periode: {r.start_date} → {r.end_date}")
    print(bar)
    print(f"  Total trades          : {r.total_trades}")
    print(f"  Wins / Losses         : {r.wins} / {r.losses}")
    print(f"  Win rate              : {r.win_rate:.1f}%")
    print(f"  Gross return          : {r.gross_return_pct:+.2f}%")
    print(f"  Net return (after fee): {r.net_return_pct:+.2f}%")
    print(f"  Max drawdown          : {r.max_drawdown_pct:.2f}%")
    print(f"  Profit factor         : {r.profit_factor:.2f}")
    print(f"  Sharpe ratio (est.)   : {r.sharpe_ratio:.2f}")
    print(f"  Rata-rata持有天数      : {r.avg_trade_days:.1f} hari")
    print(bar)

    if r.wins > 0 and r.losses > 0:
        avg_win = statistics.mean([t.gross_profit_pct for t in r.trades if t.net_profit_pct > 0])
        avg_loss = statistics.mean([t.gross_profit_pct for t in r.trades if t.net_profit_pct <= 0])
        print(f"  Rata-rata profit/win  : +{avg_win:.2f}%")
        print(f"  Rata-rata loss/loss   : {avg_loss:.2f}%")
    elif r.wins > 0:
        avg_win = statistics.mean([t.gross_profit_pct for t in r.trades if t.net_profit_pct > 0])
        print(f"  Rata-rata profit/win  : +{avg_win:.2f}%  *(tidak ada loss)*")
    print()

    # Trade log (last 20)
    if r.trades:
        print("  ── Contoh Trade Terakhir (max 20) ──")
        recent = r.trades[-20:]
        for t in recent:
            flag = "✅" if t.net_profit_pct > 0 else "❌"
            print(f"  {flag} {t.exit_reason:>16s}  "
                  f"Entry ${t.entry_price:>10.2f} → Exit ${t.exit_price:>10.2f}  "
                  f"P&L {t.net_profit_pct:+7.2f}%")
        print()


def save_csv(r: BacktestResult, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["idx","side","symbol","entry_price","exit_price","qty",
                     "fee","gross_profit_pct","net_profit_pct","exit_reason",
                     "entry_time","exit_time"])
        for t in r.trades:
            w.writerow([t.idx_entry, t.side, t.symbol, t.entry_price, t.exit_price,
                        t.qty, t.fee, t.gross_profit_pct, t.net_profit_pct,
                        t.exit_reason, t.entry_time, t.exit_time])
    print(f"  📄 Trade log saved → {p}")


def save_summary(r: BacktestResult, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = asdict(r)
    d["trades"] = [asdict(t) for t in r.trades]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print(f"  📊 Summary JSON saved → {p}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Backtest RSI+EMA Strategy for Binance Spot")
    ap.add_argument("--symbol", default="BTCUSDT", help="Symbol trading pair")
    ap.add_argument("--timeframe", default="15m", help="Kline interval (1m,5m,15m,1h,4h,1d)")
    ap.add_argument("--rsi-period", type=int, default=14)
    ap.add_argument("--ema-period", type=int, default=200)
    ap.add_argument("--oversold", type=int, default=30)
    ap.add_argument("--overbought", type=int, default=70)
    ap.add_argument("--stop-loss-pct", type=float, default=0.02)
    ap.add_argument("--trade-qty", type=float, default=0.005)
    ap.add_argument("--order-mode", choices=["market", "limit"], default="market",
                    help="Simulasi order type (default: market)")
    ap.add_argument("--start-date", default=BACKTEST_START, help="Start date YYYY-MM-DD")
    ap.add_argument("--end-date", default="2026-11-13", help="End date YYYY-MM-DD")
    ap.add_argument("--volume-filter", action="store_true", help="P1: Hanya entry dengan volume > 1.5x rata-rata 20m")
    ap.add_argument("--volume-mult", type=float, default=1.5, help="Volume multiplier threshold (default 1.5)")
    ap.add_argument("--adx-filter", action="store_true", help="P1: Hanya entry dengan ADX(14) >= min_adx")
    ap.add_argument("--min-adx", type=float, default=20.0, help="Minimum ADX threshold (default 20)")
    ap.add_argument("--output-dir", default="./backtest_results", help="Output directory")
    ap.add_argument("--multi-symbol", nargs="*", help="Run multiple symbols (space-separated)")
    args = ap.parse_args()

    symbols = args.multi_symbol if args.multi_symbol else [args.symbol]
    os.makedirs(args.output_dir, exist_ok=True)

    all_results = []
    for sym in symbols:
        r = run_backtest(
            symbol=sym,
            interval=args.timeframe,
            trade_qty=args.trade_qty,
            rsi_period=args.rsi_period,
            ema_period=args.ema_period,
            oversold=args.oversold,
            overbought=args.overbought,
            stop_loss_pct=args.stop_loss_pct,
            order_mode=args.order_mode,
            volume_filter=args.volume_filter,
            volume_avg_period=20,
            volume_multiplier=args.volume_mult,
            adx_filter=args.adx_filter,
            min_adx=args.min_adx,
            start_str=args.start_date,
            end_str=args.end_date,
        )
        print_report(r)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_sym = sym.replace("/", "-")
        flags = []
        if args.volume_filter:
            flags.append("vol")
        if args.adx_filter:
            flags.append("adx")
        suffix = f"_{'+'.join(flags)}" if flags else ""
        save_csv(r, os.path.join(args.output_dir, f"trades_{safe_sym}{suffix}_{ts}.csv"))
        save_summary(r, os.path.join(args.output_dir, f"summary_{safe_sym}{suffix}_{ts}.json"))
        all_results.append(r)

    # Aggregate multi-symbol
    if len(all_results) > 1:
        print("\n" + "═" * 55)
        print("  AGGREGATE MULTI-SYMBOL")
        print("═" * 55)
        tot_wins = sum(r.wins for r in all_results)
        tot_losses = sum(r.losses for r in all_results)
        tot_trades = sum(r.total_trades for r in all_results)
        tot_net = sum(r.net_return_pct for r in all_results) / len(all_results)
        print(f"  Symbols tested : {len(all_results)}")
        print(f"  Total trades   : {tot_trades}")
        print(f"  Avg win rate   : {tot_wins/max(tot_trades,1)*100:.1f}%")
        print(f"  Avg net return : {tot_net:+.2f}%")
        print("═" * 55)


if __name__ == "__main__":
    main()
