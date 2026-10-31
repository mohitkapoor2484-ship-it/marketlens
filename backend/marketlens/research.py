import numpy as np
import pandas as pd


def tradable_bars(payload):
    """Reconstruct as-traded volume from the vendor's split-adjusted volume.

    This is a documented vendor reconstruction, not a claim of historical vintages.
    Adjusted close is retained for display only and never used for executions.
    """
    bars = [dict(b) for b in payload["bars"]]
    if payload.get("volume_basis", "").startswith("vendor"):
        splits = [a for a in payload["actions"] if a["kind"] == "split"]
        for b in bars:
            factor = np.prod([a["ratio"] for a in splits if a["date"] > b["date"]])
            b["volume"] = b["volume"] / factor
    return bars


def wilder(series, period=14):
    # Seed with the arithmetic average, then Wilder's recursive average.
    valid = series.dropna()
    result = pd.Series(np.nan, index=series.index)
    if len(valid) >= period:
        seeded = valid.copy()
        seeded.iloc[:period - 1] = np.nan
        seeded.iloc[period - 1] = valid.iloc[:period].mean()
        result.loc[seeded.index] = seeded.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
    return result


def _indicators(frame, lookback):
    f = frame.copy()
    c = f.close
    for n in (20, 50, 200):
        f[f"sma{n}"] = c.rolling(n, min_periods=n).mean()
        f[f"ema{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean()
    delta = c.diff()
    gain, loss = wilder(delta.clip(lower=0)), wilder((-delta).clip(lower=0))
    f["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    f.loc[(loss == 0) & (gain > 0), "rsi"] = 100
    f.loc[(loss == 0) & (gain == 0), "rsi"] = 50
    f["macd"] = c.ewm(span=12, adjust=False, min_periods=12).mean() - c.ewm(span=26, adjust=False, min_periods=26).mean()
    f["macd_signal"] = f.macd.ewm(span=9, adjust=False, min_periods=9).mean()
    f["macd_hist"] = f.macd - f.macd_signal
    tr = pd.concat([f.high - f.low, (f.high - c.shift()).abs(), (f.low - c.shift()).abs()], axis=1).max(axis=1)
    f["atr"] = wilder(tr)
    f["atr_pct"] = f.atr / c * 100
    f["volume_ratio"] = f.volume / f.volume.shift().rolling(20).mean().replace(0, np.nan)
    f["volume_change_pct"] = (f.volume / f.volume.shift().replace(0, np.nan) - 1) * 100
    f["dollar_volume"] = (f.volume * c).rolling(20).mean()
    f["return63"] = c.pct_change(63, fill_method=None)
    f["volatility"] = c.pct_change(fill_method=None).rolling(20).std(ddof=1) * np.sqrt(252) * 100
    f["prior_high"] = f.high.shift().rolling(lookback).max()
    f["range_pct"] = (f.high.rolling(20).max() / f.low.rolling(20).min() - 1) * 100
    f["previous_close"] = c.shift()
    f["previous_sma20"] = f.sma20.shift()
    return f


def indicator_frame(payload, lookback=20):
    raw = pd.DataFrame(tradable_bars(payload)).set_index("date").sort_index()
    if raw.empty:
        return raw
    actions = sorted([a for a in payload.get("actions", []) if a["kind"] == "split"], key=lambda a: a["date"])
    boundaries = sorted(set([raw.index[0]] + [a["date"] for a in actions if raw.index[0] < a["date"] <= raw.index[-1]]))
    pieces = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else "9999-12-31"
        causal = raw.loc[raw.index < end].copy()
        # At each segment, only actions already effective in that segment may rebase history.
        for a in actions:
            if a["date"] <= start:
                earlier = causal.index < a["date"]
                causal.loc[earlier, ["open", "high", "low", "close"]] /= a["ratio"]
                causal.loc[earlier, "volume"] *= a["ratio"]
        computed = _indicators(causal, lookback)
        pieces.append(computed.loc[(computed.index >= start) & (computed.index < end)])
    return pd.concat(pieces).sort_index()


def finite_row(row):
    return {k: float(v) if isinstance(v, (float, int, np.number)) and np.isfinite(v) else None for k, v in row.items()}
