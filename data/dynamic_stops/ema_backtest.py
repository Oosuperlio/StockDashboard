#!/usr/bin/env python3
"""
EMA Trailing Stop Backtest — Workstream C (v3)
Tests 4 EMA trailing stop strategies on 7 stocks over 3 years.
"""

import json
import os
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
TICKERS = ["ACN", "CBRE", "CME", "CRWD", "MPWR", "SSNC", "VTRS"]
START = "2023-07-05"
END   = "2026-07-05"
OUTPUT = os.path.expanduser("~/projects/dashboard/data/dynamic_stops/ema_backtest.json")
RF_RATE = 0.05


# ── Data Download ───────────────────────────────────────────────────────────
def download_data(ticker: str) -> pd.DataFrame:
    print(f"  Downloading {ticker} ...")
    df = yf.download(ticker, start=START, end=END, auto_adjust=True, progress=False)
    if df.empty:
        raise ValueError(f"No data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df


# ── EMA Calculation ─────────────────────────────────────────────────────────
def add_emas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA20"]  = df["Close"].ewm(span=20,  adjust=False).mean()
    df["EMA50"]  = df["Close"].ewm(span=50,  adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()
    return df


# ── Universal Simulator ─────────────────────────────────────────────────────
def simulate(df: pd.DataFrame, scheme: str) -> pd.DataFrame:
    """
    Returns a DataFrame with columns:
      - position (1=in, 0=out)
      - strategy_return (daily pct return of the strategy)
      - equity (cumulative product of strategy returns)

    scheme: 'A', 'B', 'C', or 'D'
    """
    df = df.copy()
    n = len(df)
    position = np.zeros(n, dtype=int)

    in_pos = False
    buy_price = 0.0
    current_stop = None  # only for scheme D

    def get_stop(row, stop_name):
        if stop_name == "EMA20":
            return row["EMA20"]
        elif stop_name == "EMA50":
            return row["EMA50"]
        else:
            return row["EMA200"]

    for i in range(n):
        idx = df.index[i]
        close = float(df.iloc[i]["Close"])
        open_price = float(df.iloc[i]["Open"])

        # ── Buy signal: 1st of month (trading day), not in position ──
        if not in_pos and idx.day == 1:
            buy_price = open_price
            in_pos = True
            position[i] = 1
            if scheme == "D":
                current_stop = "EMA20"
            continue

        # ── If in position, check stop and month-end ──
        if in_pos:
            position[i] = 1  # start the day in position

            # Determine stop level
            if scheme == "A":
                stop_level = float(df.iloc[i]["EMA20"])
            elif scheme == "B":
                stop_level = float(df.iloc[i]["EMA50"])
            elif scheme == "C":
                stop_level = float(df.iloc[i]["EMA200"])
            else:  # D — Hybrid
                gain_pct = (close - buy_price) / buy_price * 100
                if gain_pct > 10:
                    current_stop = "EMA200"
                elif gain_pct > 5:
                    current_stop = "EMA50"
                else:
                    current_stop = "EMA20"
                stop_level = get_stop(df.iloc[i], current_stop)

            # Check trailing stop
            hit_stop = close < stop_level

            # Check month-end sell
            is_last_row = (i == n - 1)
            is_eom = False
            if not is_last_row:
                next_idx = df.index[i + 1]
                is_eom = next_idx.month != idx.month
            else:
                is_eom = True

            should_sell = hit_stop or is_eom

            if should_sell:
                in_pos = False
                # Keep position[i]=1 so the daily return loop captures
                # the Close[t]/Close[t-1] move on the sell day.
                # The sell proceeds are realized at Close[t].
                # position[i] stays 1 for the second loop; we use a
                # separate exit_signal to mark this row as the last in trade.
                # Actually, simpler: leave position[i]=1, and mark the sell.
                # After the sell day, position should be 0.
                # We handle this by NOT changing position[i] here.
                # Instead we record it in a separate array.
                pass

    # Now build daily returns from position flags.
    # We need to handle sells properly: on a sell day, position[i] is 1
    # and we capture Close[t]/Close[t-1] return. But the NEXT day should be 0.
    # Re-run: mark the day AFTER sell as 0.
    # Let's recompute position properly.
    # Actually, simpler approach: rebuild position from the logic.

    # Let's redo this properly with a 2-pass approach.
    # Pass 1: generate trades (buy dates, sell dates)
    # Pass 2: build position array and daily returns

    # Redo with cleaner design
    in_pos = False
    position2 = np.zeros(n, dtype=int)
    buy_price2 = 0.0
    current_stop = None

    for i in range(n):
        idx = df.index[i]
        close_val = float(df.iloc[i]["Close"])
        open_val = float(df.iloc[i]["Open"])

        if not in_pos and idx.day == 1:
            # Buy at open
            buy_price2 = open_val
            in_pos = True
            position2[i] = 1
            if scheme == "D":
                current_stop = "EMA20"
            continue

        if in_pos:
            position2[i] = 1  # in position today

            # Determine stop level
            if scheme == "A":
                stop_level = float(df.iloc[i]["EMA20"])
            elif scheme == "B":
                stop_level = float(df.iloc[i]["EMA50"])
            elif scheme == "C":
                stop_level = float(df.iloc[i]["EMA200"])
            else:  # D
                gain_pct = (close_val - buy_price2) / buy_price2 * 100
                if gain_pct > 10:
                    current_stop = "EMA200"
                elif gain_pct > 5:
                    current_stop = "EMA50"
                else:
                    current_stop = "EMA20"
                stop_level = get_stop(df.iloc[i], current_stop)

            hit_stop = close_val < stop_level
            is_last = (i == n - 1)
            is_eom = False
            if not is_last:
                next_idx = df.index[i + 1]
                is_eom = next_idx.month != idx.month
            else:
                is_eom = True

            if hit_stop or is_eom:
                # Sell at today's close — position remains 1 for today's return
                in_pos = False
                # Mark that tomorrow we are out (if there is a tomorrow)
                if i + 1 < n:
                    position2[i + 1] = 0  # will be overwritten if re-buy

    # Now compute daily returns from position2
    daily_rets = np.zeros(n)
    for i in range(n):
        if position2[i] == 1:
            if i == 0:
                daily_rets[i] = float(df.iloc[i]["Close"]) / float(df.iloc[i]["Open"]) - 1.0
            elif position2[i - 1] == 1:
                # Continuation: return from prev close to this close
                daily_rets[i] = float(df.iloc[i]["Close"]) / float(df.iloc[i - 1]["Close"]) - 1.0
            else:
                # New buy at open
                daily_rets[i] = float(df.iloc[i]["Close"]) / float(df.iloc[i]["Open"]) - 1.0

    eq_series = pd.Series((1.0 + daily_rets).cumprod(), index=df.index)
    df["Position"] = position2
    df["Strategy_Return"] = daily_rets
    df["Equity"] = eq_series

    return df


# ── Trade Extraction ────────────────────────────────────────────────────────
def extract_trades(df: pd.DataFrame) -> list:
    """Extract list of trade returns from position column."""
    trades = []
    in_trade = False
    buy_price = 0.0

    for i in range(len(df)):
        pos = df.iloc[i]["Position"]

        if pos == 1 and not in_trade:
            buy_price = float(df.iloc[i]["Open"])
            in_trade = True
        elif pos == 0 and in_trade:
            # Just exited — sell at previous day's close (the sell day)
            if i > 0:
                sell_price = float(df.iloc[i - 1]["Close"])
                ret = (sell_price - buy_price) / buy_price
                trades.append(ret)
            in_trade = False
        elif pos == 1 and in_trade:
            # Check if this is the last day of the last trade (end of data)
            if i == len(df) - 1:
                sell_price = float(df.iloc[i]["Close"])
                ret = (sell_price - buy_price) / buy_price
                trades.append(ret)
                in_trade = False

    return trades


# ── Metrics ─────────────────────────────────────────────────────────────────
def compute_metrics(trades: list, df: pd.DataFrame) -> dict:
    n_trades = len(trades)

    if n_trades == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_win_loss_ratio": 0.0,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
        }

    wins = [r for r in trades if r > 0]
    losses = [r for r in trades if r <= 0]
    win_rate = len(wins) / n_trades * 100.0
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 0.0
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else (float('inf') if avg_win > 0 else 0.0)
    total_return = (np.prod([1.0 + r for r in trades]) - 1.0) * 100.0

    # Max drawdown from strategy equity curve
    equity = df["Equity"].values.astype(np.float64)
    running_max = np.maximum.accumulate(equity)
    drawdown = np.where(running_max > 0, (equity - running_max) / running_max, 0.0)
    max_dd = float(abs(np.min(drawdown)) * 100.0)

    # Sharpe ratio from strategy daily returns
    strat_rets = df["Strategy_Return"].values.astype(np.float64)
    excess = strat_rets - RF_RATE / 252
    std_rets = np.std(strat_rets)
    if std_rets > 0:
        sharpe = float(np.sqrt(252) * np.mean(excess) / std_rets)
    else:
        sharpe = 0.0

    return {
        "total_trades": n_trades,
        "win_rate": round(win_rate, 2),
        "avg_win_loss_ratio": round(win_loss_ratio, 4),
        "total_return_pct": round(total_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 4),
    }


# ── Main ────────────────────────────────────────────────────────────────────
def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)

    schemes = ["A_EMA20", "B_EMA50", "C_EMA200", "D_Hybrid"]
    scheme_keys = {"A_EMA20": "A", "B_EMA50": "B", "C_EMA200": "C", "D_Hybrid": "D"}

    all_results = {}
    recommendation_map = {}

    for ticker in TICKERS:
        print(f"\n{'='*60}")
        print(f"Processing {ticker} ...")
        print(f"{'='*60}")

        df_raw = download_data(ticker)
        print(f"  Rows: {len(df_raw)}, Date: {df_raw.index[0].date()} → {df_raw.index[-1].date()}")

        df = add_emas(df_raw)

        ticker_results = {}
        best_sharpe = -999.0
        best_scheme = None

        for sn in schemes:
            sk = scheme_keys[sn]
            df_sim = simulate(df, sk)
            trades = extract_trades(df_sim)
            metrics = compute_metrics(trades, df_sim)
            metrics["scheme"] = sn
            ticker_results[sn] = metrics
            print(
                f"  {sn:>12s} | Trades={metrics['total_trades']:3d}  "
                f"Win={metrics['win_rate']:5.1f}%  "
                f"W/L={metrics['avg_win_loss_ratio']:>6.2f}  "
                f"Ret={metrics['total_return_pct']:>7.2f}%  "
                f"DD={metrics['max_drawdown_pct']:>5.1f}%  "
                f"SR={metrics['sharpe_ratio']:>6.3f}"
            )
            if metrics["sharpe_ratio"] > best_sharpe:
                best_sharpe = metrics["sharpe_ratio"]
                best_scheme = sn

        all_results[ticker] = ticker_results
        recommendation_map[ticker] = best_scheme
        print(f"  ★ Best: {best_scheme} (Sharpe={best_sharpe:.3f})")

    # ── Assemble output ─────────────────────────────────────────────────────
    output = {
        "metadata": {
            "backtest": "EMA Trailing Stop Backtest",
            "tickers": TICKERS,
            "period": f"{START} to {END}",
            "generated_at": datetime.now().isoformat(),
        },
        "strategies": {
            "A_EMA20":  "Buy 1st-of-month open. Sell when close < EMA(20) or at month-end close.",
            "B_EMA50":  "Buy 1st-of-month open. Sell when close < EMA(50) or at month-end close.",
            "C_EMA200": "Buy 1st-of-month open. Sell when close < EMA(200) or at month-end close.",
            "D_Hybrid": "Buy 1st-of-month open. Stepped stops: EMA20 → EMA50 at +5% → EMA200 at +10%, revert below +5%.",
        },
        "results": all_results,
        "recommendations": recommendation_map,
        "comparison": {"strategy_summary": {}},
    }

    for sn in schemes:
        sharpe_list = []
        ret_list = []
        dd_list = []
        for t in TICKERS:
            m = all_results[t][sn]
            sharpe_list.append(m["sharpe_ratio"])
            ret_list.append(m["total_return_pct"])
            dd_list.append(m["max_drawdown_pct"])
        output["comparison"]["strategy_summary"][sn] = {
            "avg_sharpe": round(float(np.mean(sharpe_list)), 4),
            "avg_return_pct": round(float(np.mean(ret_list)), 2),
            "avg_max_dd_pct": round(float(np.mean(dd_list)), 2),
            "stocks_best_for": [t for t in TICKERS if recommendation_map[t] == sn],
        }

    with open(OUTPUT, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Results → {OUTPUT}")
    print(f"{'='*60}")

    # Final table
    print(f"\n{'='*60}")
    print("FINAL RECOMMENDATIONS (by best Sharpe)")
    print(f"{'='*60}")
    print(f"{'Ticker':>6s}  {'Best':>12s}  {'Sharpe':>8s}  {'Return%':>8s}  {'MaxDD%':>7s}  {'Trades':>7s}")
    print(f"{'-'*6}  {'-'*12}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*7}")
    for t in TICKERS:
        bs = recommendation_map[t]
        m = all_results[t][bs]
        print(f"{t:>6s}  {bs:>12s}  {m['sharpe_ratio']:>8.3f}  {m['total_return_pct']:>8.2f}  "
              f"{m['max_drawdown_pct']:>7.2f}  {m['total_trades']:>7d}")

    # Scheme ranking
    print(f"\n{'='*60}")
    print("OVERALL SCHEME RANKING")
    print(f"{'='*60}")
    for sn in schemes:
        s = output["comparison"]["strategy_summary"][sn]
        matches = s["stocks_best_for"]
        print(f"  {sn:>12s}: avg SR={s['avg_sharpe']:.4f}  avg Ret={s['avg_return_pct']:>7.2f}%  "
              f"avg DD={s['avg_max_dd_pct']:>5.1f}%  best_for={matches}")

    return output


if __name__ == "__main__":
    main()
