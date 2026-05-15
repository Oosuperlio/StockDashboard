"""
hk_data_source.py — 香港股票數據源
=================================
優先使用騰訊財經實時報價 API，失敗時退回到 Yahoo Finance。
騰訊接口穩定且無 rate limit，適合補全 Yahoo 被限流後的 HK 股數據。
"""

import requests
import pandas as pd
import json
from datetime import date, datetime
from typing import Optional, List, Dict, Any

TENcent_REALTIME_URL = "https://qt.gtimg.cn/q=hk{code}"
TENcent_HIST_URL = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?_var=kline_dayqfq&param=hk{code},day,,,{days},qfq"

# 騰訊財經字段索引（0-based）
# 0: 名稱 3: 代碼 4: 現價 5: 昨收 6: 開盤 7: 成交量
# 31: 最高 32: 最低 33: 時間（格式：YYYY/MM/DD HH:MM:SS）
FIELDS = {
    "name":     1,
    "code":     3,
    "close":    4,
    "prev":     5,
    "open":     6,
    "volume":   7,
    "high":    31,
    "low":     32,
    "datetime": 33,
}


def _parse_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_int(val: str) -> int:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def fetch_realtime_hk(code: str) -> Optional[Dict[str, Any]]:
    """
    獲取單隻 HK 股票實時數據（騰訊財經實時行情）。
    code: 不帶前綴，例如 '00700'、'03698'、'00005'
    返回: OHLCV dict 或 None
    """
    url = TENcent_REALTIME_URL.format(code=code)
    try:
        resp = requests.get(url, timeout=5, headers={"Referer": "https://finance.qq.com"})
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception:
        return None

    # 解析 v_hkXXXXX="..."（分割用字串不以為單/雙引號混用）
    marker = '="'
    if marker not in text:
        return None

    raw = text.split(marker, 1)[1].rstrip('"; ')
    parts = raw.split("~")
    if len(parts) < 40:
        return None

    dt_str = parts[FIELDS["datetime"]]
    try:
        dt = datetime.strptime(dt_str, "%Y/%m/%d %H:%M:%S")
        trade_date = dt.date()
    except (ValueError, TypeError):
        trade_date = date.today()

    return {
        "symbol":     f"hk{code.upper()}",
        "trade_date": trade_date,
        "open":       _parse_float(parts[FIELDS["open"]]),
        "high":       _parse_float(parts[FIELDS["high"]]),
        "low":        _parse_float(parts[FIELDS["low"]]),
        "close":      _parse_float(parts[FIELDS["close"]]),
        "prev_close": _parse_float(parts[FIELDS["prev"]]),
        "volume":     _parse_int(parts[FIELDS["volume"]]),
        "currency":   "HKD",
        "source":     "tencent",
    }


def fetch_historical_hk(code: str, days: int = 30) -> List[Dict[str, Any]]:
    """
    騰訊財經歷史K線接口。
    返回: List of OHLCV dicts (newest-first)
    """
    results = []
    url = TENcent_HIST_URL.format(code=code.upper(), days=days)
    try:
        resp = requests.get(url, timeout=8, headers={"Referer": "https://finance.qq.com"})
        resp.raise_for_status()
        text = resp.text.strip()
        # 回應格式: var kline_dayqfq={...}  → 取 = 後的 JSON
        if "=" in text:
            json_str = text.split("=", 1)[1]
        else:
            json_str = text
        data = json.loads(json_str)
    except Exception:
        return results

    try:
        inner = data.get("data", {}).get(f"hk{code.upper()}", {})
        # 優先取 qfqday（前覆權），fallback 到 day（原始）
        for key in ("qfqday", "day"):
            if key in inner:
                klines = inner[key]
                break
        else:
            klines = []
    except Exception:
        return results

    for row in reversed(klines):  # oldest-first → newest-first
        try:
            dt_str, o, c, h, l, vol = row[:6]
            trade_date = datetime.strptime(str(dt_str), "%Y-%m-%d").date()
            results.append({
                "symbol":     f"hk{code.upper()}",
                "trade_date": trade_date,
                "open":       float(o),
                "high":       float(h),
                "low":        float(l),
                "close":      float(c),
                "volume":     int(float(vol)),
                "currency":   "HKD",
                "source":     "tencent",
            })
        except (ValueError, TypeError, IndexError):
            continue

    return results
