"""
portfolio_tab.py — Portfolio tab UI for the Stock Dashboard

Provides:
  1. Portfolio overview — total P&L, best/worst performers
  2. Open positions table — edit, add trades, close
  3. Add new position form
  4. Realized P&L history
  5. Custom date range return calculator
"""

from __future__ import annotations

import streamlit as st
import pandas as pd

from database.portfolio import PortfolioManager, Trade


# ─── Initialize ──────────────────────────────────────────────────────────────

@st.cache_resource
def get_portfolio_manager() -> PortfolioManager:
    return PortfolioManager()


def render_portfolio_tab():
    pm = get_portfolio_manager()

    st.markdown("## 🗂️ 持倉管理")

    # ── Top-level Action Bar ─────────────────────────────────────────────
    col_action1, col_action2, col_action3 = st.columns([2, 2, 3])
    with col_action1:
        with st.popover("➕ 新增持倉", use_container_width=True):
            _render_add_position_form(pm)
    with col_action2:
        with st.popover("📅 日期區間回報", use_container_width=True):
            _render_date_range_form(pm)

    st.markdown("---")

    # ── Portfolio Overview Cards ─────────────────────────────────────────
    _render_overview(pm)

    st.markdown("---")

    # ── Open Positions ───────────────────────────────────────────────────
    _render_open_positions(pm)

    st.markdown("---")

    # ── Closed Positions / Realized P&L ──────────────────────────────────
    _render_closed_positions(pm)


# ═══════════════════════════════════════════════════════════════════════════════
#  Internal Renders
# ═══════════════════════════════════════════════════════════════════════════════


def _render_overview(pm: PortfolioManager):
    open_positions = pm.get_open_positions()
    total_cost = 0.0
    total_market = 0.0
    total_unrealized = 0.0

    rows = []
    for pos in open_positions:
        r = pm.get_unrealized_pnl(pos.id)
        if r:
            total_cost += r["cost_basis"]
            total_market += r["market_value"]
            total_unrealized += r["unrealized_pnl"]
            rows.append({
                "Ticker": r["ticker"],
                "Shares": r["shares"],
                "Avg Cost": r["avg_cost"],
                "Current": r["current_price"],
                "Market Value": r["market_value"],
                "Unrealized P&L": r["unrealized_pnl"],
                "P&L %": r["unrealized_pnl_pct"],
            })

    col1, col2, col3, col4 = st.columns(4)
    total_cost_display = f"${total_cost:,.2f}"
    total_market_display = f"${total_market:,.2f}"
    total_pnl = total_market - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    total_realized = pm.get_total_realized_pnl()

    with col1:
        st.markdown(
            f'<div class="card"><div class="card-header">💰 總成本</div>'
            f'<div class="card-value">{total_cost_display}</div></div>',
            unsafe_allow_html=True,
        )
    with col2:
        st.markdown(
            f'<div class="card"><div class="card-header">📊 總市值</div>'
            f'<div class="card-value">{total_market_display}</div></div>',
            unsafe_allow_html=True,
        )
    with col3:
        pnl_color = "#48bb78" if total_pnl >= 0 else "#fc8181"
        pnl_arrow = "▲" if total_pnl >= 0 else "▼"
        st.markdown(
            f'<div class="card"><div class="card-header">📈 未實現損益</div>'
            f'<div class="card-value" style="color:{pnl_color}">{pnl_arrow} ${abs(total_pnl):,.2f}</div>'
            f'<div class="card-delta" style="color:{pnl_color}">{pnl_arrow} {abs(total_pnl_pct):.2f}%</div></div>',
            unsafe_allow_html=True,
        )
    with col4:
        realized_color = "#48bb78" if total_realized >= 0 else "#fc8181"
        st.markdown(
            f'<div class="card"><div class="card-header">✅ 已實現損益</div>'
            f'<div class="card-value" style="color:{realized_color}">${total_realized:,.2f}</div></div>',
            unsafe_allow_html=True,
        )

    if rows:
        # Best & worst performers
        df = pd.DataFrame(rows)
        best = df.loc[df["P&L %"].idxmax()]
        worst = df.loc[df["P&L %"].idxmin()]

        col_b, col_w = st.columns(2)
        with col_b:
            st.markdown(
                f'<div class="card" style="border-left: 3px solid #48bb78;">'
                f'<div class="card-header">🏆 最佳表現</div>'
                f'<div style="font-size: 16px; font-weight: 600; color:#48bb78;">{best["Ticker"]}</div>'
                f'<div style="font-size: 13px; color:#a0aec0;">P&L: <b>+{best["P&L %"]:.2f}%</b> '
                f'(${best["Unrealized P&L"]:+,.2f})</div></div>',
                unsafe_allow_html=True,
            )
        with col_w:
            st.markdown(
                f'<div class="card" style="border-left: 3px solid #fc8181;">'
                f'<div class="card-header">📉 最差表現</div>'
                f'<div style="font-size: 16px; font-weight: 600; color:#fc8181;">{worst["Ticker"]}</div>'
                f'<div style="font-size: 13px; color:#a0aec0;">P&L: <b>{worst["P&L %"]:+.2f}%</b> '
                f'(${worst["Unrealized P&L"]:+,.2f})</div></div>',
                unsafe_allow_html=True,
            )


def _render_open_positions(pm: PortfolioManager):
    st.markdown("### 📋 持倉明細")

    positions = pm.get_open_positions()
    if not positions:
        st.info("目前沒有持倉。點擊「新增持倉」開始記錄。")
        return

    # Tab for each open position
    tabs = st.tabs([f"{p.ticker} [{p.id}]" for p in positions])
    for i, pos in enumerate(positions):
        with tabs[i]:
            col_info, col_trades = st.columns([2, 2])

            with col_info:
                r = pm.get_unrealized_pnl(pos.id)
                if r:
                    pnl_color = "#48bb78" if r["unrealized_pnl"] >= 0 else "#fc8181"
                    st.markdown(
                        f'<div class="card">'
                        f'  <div class="card-header">{pos.ticker} 持倉概覽</div>'
                        f'  <div class="metric-pair"><span class="metric-label">持倉數量</span>'
                        f'<span class="metric-val">{r["shares"]:.2f}</span></div>'
                        f'  <div class="metric-pair"><span class="metric-label">平均成本</span>'
                        f'<span class="metric-val">${r["avg_cost"]:.2f}</span></div>'
                        f'  <div class="metric-pair"><span class="metric-label">現價</span>'
                        f'<span class="metric-val">${r["current_price"]:.2f}</span></div>'
                        f'  <div class="metric-pair"><span class="metric-label">市值</span>'
                        f'<span class="metric-val">${r["market_value"]:,.2f}</span></div>'
                        f'  <div class="metric-pair"><span class="metric-label">未實現損益</span>'
                        f'<span class="metric-val" style="color:{pnl_color}">'
                        f'{"▲" if r["unrealized_pnl"]>=0 else "▼"} ${abs(r["unrealized_pnl"]):,.2f} '
                        f'({r["unrealized_pnl_pct"]:+.2f}%)</span></div>'
                        f'  <div class="metric-pair"><span class="metric-label">已實現損益</span>'
                        f'<span class="metric-val">${pm.get_realized_pnl(pos.id):,.2f}</span></div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            with col_trades:
                # Trade log
                trades = pos.trades
                if trades:
                    trade_data = []
                    for t in trades:
                        trade_data.append({
                            "Date": t.date,
                            "Type": "🟢 Buy" if t.type == "buy" else "🔴 Sell",
                            "Shares": t.shares,
                            "Price": f"${t.price:.2f}",
                            "Total": f"${t.shares * t.price:,.2f}",
                        })
                    st.markdown("**交易記錄**")
                    st.dataframe(
                        pd.DataFrame(trade_data),
                        use_container_width=True,
                        hide_index=True,
                        height=min(40 + 35 * len(trade_data), 250),
                    )
                else:
                    st.info("暫無交易記錄")

            # Actions
            st.markdown("---")
            col_act1, col_act2, col_act3, col_act4 = st.columns(4)
            with col_act1:
                with st.popover("➕ 買入", use_container_width=True):
                    _render_buy_form(pm, pos.id)
            with col_act2:
                with st.popover("➖ 賣出部分", use_container_width=True):
                    _render_sell_partial_form(pm, pos.id)
            with col_act3:
                if st.button("🔴 全部平倉", use_container_width=True, key=f"close_{pos.id}"):
                    if pm.close_position(pos.id):
                        st.success(f"{pos.ticker} 已全部平倉！")
                        st.rerun()
                    else:
                        st.error("平倉失敗，無法獲取當前價格")
            with col_act4:
                if st.button("🗑️ 刪除持倉", use_container_width=True, key=f"del_{pos.id}"):
                    if pm.remove_position(pos.id):
                        st.success(f"{pos.ticker} 持倉已刪除")
                        st.rerun()


def _render_closed_positions(pm: PortfolioManager):
    closed = pm.get_closed_positions()
    if not closed:
        return

    st.markdown("### 📦 已平倉")
    for pos in closed:
        realized = pm.get_realized_pnl(pos.id)
        with st.expander(f"{pos.ticker} — 已實現損益: ${realized:+,.2f}"):
            trades = pos.trades
            if trades:
                trade_data = []
                for t in trades:
                    trade_data.append({
                        "Date": t.date,
                        "Type": "🟢 Buy" if t.type == "buy" else "🔴 Sell",
                        "Shares": t.shares,
                        "Price": f"${t.price:.2f}",
                        "Total": f"${t.shares * t.price:,.2f}",
                    })
                st.dataframe(
                    pd.DataFrame(trade_data),
                    use_container_width=True,
                    hide_index=True,
                )
            if st.button("🗑️ 刪除", key=f"del_closed_{pos.id}"):
                pm.remove_position(pos.id)
                st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
#  Forms
# ═══════════════════════════════════════════════════════════════════════════════


def _render_add_position_form(pm: PortfolioManager):
    ticker = st.text_input("股票代碼", placeholder="例: AAPL", key="add_pos_ticker").strip().upper()
    col1, col2 = st.columns(2)
    with col1:
        shares = st.number_input("買入股數", min_value=0.0, step=1.0, key="add_pos_shares")
    with col2:
        price = st.number_input("買入價格 ($)", min_value=0.0, step=0.01, format="%.2f", key="add_pos_price")
    date_val = st.date_input("買入日期", value="today", key="add_pos_date")
    notes = st.text_input("備註（可選）", key="add_pos_notes")
    if st.button("✅ 確認新增", use_container_width=True, key="add_pos_btn"):
        if not ticker or shares <= 0 or price <= 0:
            st.error("請填寫股票代碼、股數和價格")
            return
        pm.add_position(ticker, trades=[
            Trade(
                date=date_val.strftime("%Y-%m-%d"),
                type="buy",
                shares=shares,
                price=price,
                notes=notes,
            )
        ])
        st.success(f"{ticker} 持倉已建立！")
        st.rerun()


def _render_buy_form(pm: PortfolioManager, position_id: str):
    col1, col2 = st.columns(2)
    with col1:
        shares = st.number_input("買入股數", min_value=0.0, step=1.0, key=f"buy_shares_{position_id}")
    with col2:
        price = st.number_input("買入價格 ($)", min_value=0.0, step=0.01, format="%.2f", key=f"buy_price_{position_id}")
    date_val = st.date_input("買入日期", value="today", key=f"buy_date_{position_id}")
    if st.button("✅ 確認", use_container_width=True, key=f"buy_btn_{position_id}"):
        if shares <= 0 or price <= 0:
            st.error("請填寫股數和價格")
            return
        pm.add_trade(position_id, Trade(
            date=date_val.strftime("%Y-%m-%d"),
            type="buy",
            shares=shares,
            price=price,
        ))
        st.success("買入記錄已新增")
        st.rerun()


def _render_sell_partial_form(pm: PortfolioManager, position_id: str):
    pos = pm.get_position(position_id)
    if not pos:
        st.error("持倉不存在")
        return
    max_sell = pos.net_shares
    col1, col2 = st.columns(2)
    with col1:
        shares = st.number_input(
            "賣出股數", min_value=0.0, max_value=max_sell, step=1.0,
            key=f"sell_shares_{position_id}",
        )
    with col2:
        price = st.number_input(
            "賣出價格 ($)", min_value=0.0, step=0.01, format="%.2f",
            key=f"sell_price_{position_id}",
        )
    date_val = st.date_input("賣出日期", value="today", key=f"sell_date_{position_id}")
    if st.button("✅ 確認賣出", use_container_width=True, key=f"sell_btn_{position_id}"):
        if shares <= 0 or price <= 0:
            st.error("請填寫股數和價格")
            return
        pm.add_trade(position_id, Trade(
            date=date_val.strftime("%Y-%m-%d"),
            type="sell",
            shares=shares,
            price=price,
        ))
        if shares >= max_sell:
            st.success(f"全部平倉完成！已實現損益: ${pm.get_realized_pnl(position_id):+,.2f}")
        else:
            st.success(f"賣出 {shares} 股完成")
        st.rerun()


def _render_date_range_form(pm: PortfolioManager):
    st.markdown("**查詢日期區間回報**")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("開始日期", value=None, key="dr_start")
    with col2:
        end_date = st.date_input("結束日期", value=None, key="dr_end")

    if st.button("📊 計算回報", use_container_width=True, key="dr_btn"):
        if not start_date or not end_date:
            st.error("請選擇日期範圍")
            return
        if start_date >= end_date:
            st.error("結束日期必須晚於開始日期")
            return

        results = pm.get_date_range_return(
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        )
        if not results:
            st.info("該日期區間內無持倉資料")
            return

        df = pd.DataFrame(results)
        st.dataframe(
            df.rename(columns={
                "ticker": "股票",
                "start_price": "開始價",
                "end_price": "結束價",
                "price_return_pct": "回報率 %",
                "pnl_dollars": "盈虧 ($)",
            }),
            use_container_width=True,
            hide_index=True,
        )
        total_pnl = df["pnl_dollars"].sum()
        avg_return = df["price_return_pct"].mean()
        pnl_color = "#48bb78" if total_pnl >= 0 else "#fc8181"
        st.markdown(
            f'<div class="card">'
            f'<div class="card-header">📈 區間匯總</div>'
            f'<div style="display:flex; gap: 24px;">'
            f'<div>平均回報: <b>{avg_return:+.2f}%</b></div>'
            f'<div>總盈虧: <b style="color:{pnl_color}">${total_pnl:+,.2f}</b></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
