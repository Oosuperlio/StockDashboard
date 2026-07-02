"""
portfolio.py — Portfolio position tracker (JSON-backed)

Stores positions and trades in a JSON file at data/portfolio/portfolio.json.
Each position has a unique ID, a ticker, and a list of trades (buy/sell).
Supports:
  - Add position (open)
  - Add trade to existing position (buy more or sell partial)
  - Close position fully
  - Calculate unrealized/realized P&L
  - Calculate return over custom date range
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict

import yfinance as yf
import pandas as pd

# ─── Paths ───────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PORTFOLIO_DIR = DATA_DIR / "portfolio"
PORTFOLIO_FILE = PORTFOLIO_DIR / "portfolio.json"


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    date: str                # "YYYY-MM-DD"
    type: str                # "buy" or "sell"
    shares: float
    price: float             # per share
    fees: float = 0.0
    notes: str = ""


@dataclass
class Position:
    id: str
    ticker: str
    trades: List[Trade] = field(default_factory=list)

    @property
    def status(self) -> str:
        """open or closed — computed from net shares."""
        net = sum(t.shares if t.type == "buy" else -t.shares for t in self.trades)
        return "open" if net > 0 else "closed"

    @property
    def net_shares(self) -> float:
        return sum(t.shares if t.type == "buy" else -t.shares for t in self.trades)

    @property
    def total_cost(self) -> float:
        """Total cost basis of remaining open shares (FIFO)."""
        remaining = self.net_shares
        if remaining <= 0:
            return 0.0
        cost = 0.0
        for t in self.trades:
            if t.type == "buy" and remaining > 0:
                taken = min(t.shares, remaining)
                cost += taken * t.price + (taken / t.shares) * t.fees if t.shares > 0 else 0
                remaining -= taken
        return cost

    @property
    def avg_cost(self) -> float:
        net = self.net_shares
        return self.total_cost / net if net > 0 else 0.0

    @property
    def realized_pnl(self) -> float:
        """Calculate realized P&L from closed trades (FIFO)."""
        buys = []  # list of (shares, price, fees_alloc)
        realized = 0.0
        for t in self.trades:
            if t.type == "buy":
                buys.append([t.shares, t.price, t.fees])
            elif t.type == "sell":
                remaining_to_sell = t.shares
                while remaining_to_sell > 0 and buys:
                    batch = buys[0]
                    taken = min(batch[0], remaining_to_sell)
                    cost_basis = taken * batch[1] + (taken / batch[0]) * batch[2] if batch[0] > 0 else 0
                    proceeds = taken * t.price - (taken / t.shares) * t.fees if t.shares > 0 else 0
                    realized += proceeds - cost_basis
                    batch[0] -= taken
                    remaining_to_sell -= taken
                    if batch[0] <= 0:
                        buys.pop(0)
        return realized

    @property
    def trades_history(self) -> List[Dict]:
        return [asdict(t) for t in self.trades]


# ─── Portfolio Manager ────────────────────────────────────────────────────────

class PortfolioManager:
    def __init__(self):
        self._ensure_dir()
        self.positions: List[Position] = []
        self._load()

    # ── CRUD ─────────────────────────────────────────────────────────────

    def add_position(self, ticker: str, trades: Optional[List[Trade]] = None) -> Position:
        """Create a new position with optional initial trades."""
        pos = Position(
            id=str(uuid.uuid4())[:8],
            ticker=ticker.upper(),
            trades=trades or [],
        )
        self.positions.append(pos)
        self._save()
        return pos

    def add_trade(self, position_id: str, trade: Trade) -> bool:
        """Add a trade to an existing position."""
        pos = self._find(position_id)
        if not pos:
            return False
        pos.trades.append(trade)
        self._save()
        return True

    def remove_position(self, position_id: str) -> bool:
        """Delete a position entirely."""
        pos = self._find(position_id)
        if not pos:
            return False
        self.positions.remove(pos)
        self._save()
        return True

    def close_position(self, position_id: str) -> bool:
        """Close an open position by selling all remaining shares at current price."""
        pos = self._find(position_id)
        if not pos or pos.status != "open":
            return False
        # Fetch current price
        current_price = self._get_current_price(pos.ticker)
        if current_price is None:
            return False
        pos.trades.append(Trade(
            date=datetime.now().strftime("%Y-%m-%d"),
            type="sell",
            shares=pos.net_shares,
            price=current_price,
        ))
        self._save()
        return True

    def get_position(self, position_id: str) -> Optional[Position]:
        return self._find(position_id)

    def get_open_positions(self) -> List[Position]:
        return [p for p in self.positions if p.status == "open"]

    def get_closed_positions(self) -> List[Position]:
        return [p for p in self.positions if p.status == "closed"]

    def get_all_positions(self) -> List[Position]:
        return self.positions

    # ── P&L ──────────────────────────────────────────────────────────────

    def get_unrealized_pnl(self, position_id: str) -> Optional[Dict]:
        """Calculate unrealized P&L for an open position."""
        pos = self._find(position_id)
        if not pos or pos.status != "open":
            return None
        current_price = self._get_current_price(pos.ticker)
        if current_price is None:
            return None
        net = pos.net_shares
        market_value = net * current_price
        cost = pos.total_cost
        pnl = market_value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
        return {
            "ticker": pos.ticker,
            "shares": net,
            "avg_cost": round(pos.avg_cost, 2),
            "current_price": round(current_price, 2),
            "market_value": round(market_value, 2),
            "cost_basis": round(cost, 2),
            "unrealized_pnl": round(pnl, 2),
            "unrealized_pnl_pct": round(pnl_pct, 2),
        }

    def get_realized_pnl(self, position_id: str) -> float:
        """Get realized P&L from closed trades."""
        pos = self._find(position_id)
        if not pos:
            return 0.0
        return round(pos.realized_pnl, 2)

    def get_total_unrealized_pnl(self) -> Dict:
        """Sum of all open position P&L."""
        total_cost = 0.0
        total_market = 0.0
        for pos in self.get_open_positions():
            r = self.get_unrealized_pnl(pos.id)
            if r:
                total_cost += r["cost_basis"]
                total_market += r["market_value"]
        pnl = total_market - total_cost
        pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0.0
        return {
            "total_cost": round(total_cost, 2),
            "total_market_value": round(total_market, 2),
            "total_unrealized_pnl": round(pnl, 2),
            "total_unrealized_pnl_pct": round(pnl_pct, 2),
        }

    def get_total_realized_pnl(self) -> float:
        """Sum of all positions' realized P&L."""
        return round(sum(p.realized_pnl for p in self.positions), 2)

    # ── Date Range Return ────────────────────────────────────────────────

    def get_date_range_return(self, start_date: str, end_date: str) -> List[Dict]:
        """
        Calculate return over custom date range for each open position.
        Uses historical price data from yfinance.
        Returns percentage and dollar return over the period.
        """
        results = []
        for pos in self.get_open_positions():
            try:
                stock = yf.Ticker(pos.ticker)
                hist = stock.history(start=start_date, end=end_date)
                if hist.empty:
                    continue
                start_price = hist["Close"].iloc[0]
                end_price = hist["Close"].iloc[-1]
                # For the position held during this period, use the actual shares
                # But the position may have been opened/closed during the period
                # Simplification: calculate price return + if position existed
                price_return_pct = ((end_price - start_price) / start_price) * 100
                # Estimate P&L based on shares held at end of period
                shares = pos.net_shares
                pnl_dollars = shares * (end_price - start_price)
                results.append({
                    "ticker": pos.ticker,
                    "start_date": start_date,
                    "end_date": end_date,
                    "start_price": round(start_price, 2),
                    "end_price": round(end_price, 2),
                    "price_return_pct": round(price_return_pct, 2),
                    "shares": shares,
                    "pnl_dollars": round(pnl_dollars, 2),
                })
            except Exception:
                continue
        return results

    # ── Persistence ──────────────────────────────────────────────────────

    def _ensure_dir(self):
        PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)

    def _save(self):
        data = []
        for p in self.positions:
            data.append({
                "id": p.id,
                "ticker": p.ticker,
                "trades": [asdict(t) for t in p.trades],
            })
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self):
        if not PORTFOLIO_FILE.exists():
            self.positions = []
            return
        try:
            with open(PORTFOLIO_FILE) as f:
                data = json.load(f)
            self.positions = []
            for item in data:
                trades = [Trade(**t) for t in item.get("trades", [])]
                self.positions.append(Position(
                    id=item["id"],
                    ticker=item["ticker"],
                    trades=trades,
                ))
        except Exception:
            self.positions = []

    def _find(self, position_id: str) -> Optional[Position]:
        for p in self.positions:
            if p.id == position_id:
                return p
        return None

    @staticmethod
    def _get_current_price(ticker: str) -> Optional[float]:
        try:
            stock = yf.Ticker(ticker)
            info = stock.fast_info
            return float(info.last_price)
        except Exception:
            try:
                info = stock.info
                return float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
            except Exception:
                return None


# ─── Demo / Quick Test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    pm = PortfolioManager()
    print(f"Positions loaded: {len(pm.positions)}")
    for p in pm.get_all_positions():
        print(f"  {p.ticker} [{p.id}]: {p.status}, {p.net_shares:.2f} sh, avg_cost=${p.avg_cost:.2f}")
        r = pm.get_unrealized_pnl(p.id)
        if r:
            print(f"    Unrealized P&L: ${r['unrealized_pnl']:.2f} ({r['unrealized_pnl_pct']:.1f}%)")
        print(f"    Realized P&L: ${pm.get_realized_pnl(p.id):.2f}")
