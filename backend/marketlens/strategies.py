from .domain import RULE_VERSION, ScanRules, StrategyParams

STRATEGIES = {
    "breakout": "Close above the preceding N-session high, with relative volume ≥ 1.2×",
    "momentum_continuation": "Close > SMA20 > SMA50 > SMA200, RSI 55–72, MACD histogram positive",
    "moving_average_pullback": "Rising long trend; low tests SMA20, close recovers above SMA20, RSI 40–65",
    "volume_breakout": "Relative volume ≥ 2×, up close above SMA50",
    "trend_reversal_candidate": "Close crosses above SMA20, MACD histogram positive and RSI > 50; unconfirmed reversal",
    "consolidation": "20-session high/low range < 8%, ATR/close < 2.5%, price above SMA50",
}


def detected(row):
    r = row
    needed = ["sma200", "sma50", "sma20", "rsi", "macd_hist", "atr", "volume_ratio", "prior_high"]
    if any(r.get(k) is None for k in needed):
        return []
    trend = r["close"] > r["sma20"] > r["sma50"] > r["sma200"]
    checks = {
        "breakout": r["close"] > r["prior_high"] and r["volume_ratio"] >= 1.2,
        "momentum_continuation": trend and 55 <= r["rsi"] <= 72 and r["macd_hist"] > 0,
        "moving_average_pullback": r["sma50"] > r["sma200"] and r["low"] <= r["sma20"] * 1.01 and r["close"] > r["sma20"] and 40 <= r["rsi"] <= 65,
        "volume_breakout": r["volume_ratio"] >= 2 and r["close"] > r["previous_close"] and r["close"] > r["sma50"],
        "trend_reversal_candidate": r["previous_close"] <= r["previous_sma20"] and r["close"] > r["sma20"] and r["macd_hist"] > 0 and r["rsi"] > 50,
        "consolidation": r["range_pct"] < 8 and r["atr_pct"] < 2.5 and r["close"] > r["sma50"],
    }
    return [k for k, yes in checks.items() if yes]


def trade_plan(row, params=None):
    p = params or StrategyParams()
    entry = row["high"] + max(row["close"] * .001, .01)
    stop = max(.0001, entry - p.stop_atr * row["atr"])
    risk = entry - stop
    return {"hypothetical": True, "type": "buy_stop", "entry": entry, "stop": stop,
            "target": entry + p.target_r * risk, "target_1": entry + risk, "reward_risk": p.target_r,
            "invalidation": "Stop level breached; entry expires after configured sessions",
            "entry_condition": "A subsequent eligible session trades at or above the trigger; gaps fill at the worse opening price"}


def rank_candidate(instrument, row, relative_strength, market_cap, scan_rules=None, params=None):
    rules = scan_rules or ScanRules()
    setups = detected(row)
    exclusions = []
    if not setups:
        exclusions.append("No rule-defined setup")
    if row["close"] < rules.min_price:
        exclusions.append("Price below minimum in source currency")
    if row.get("dollar_volume") is None or row["dollar_volume"] < rules.min_dollar_volume:
        exclusions.append("Insufficient 20-session dollar liquidity in source currency")
    if rules.min_market_cap > 0 and (market_cap is None or market_cap < rules.min_market_cap):
        exclusions.append("Market capitalisation unavailable or below minimum")
    if row.get("atr_pct") is None or not rules.min_atr_pct <= row["atr_pct"] <= rules.max_atr_pct:
        exclusions.append("ATR outside configured bounds")
    checks = [row.get("close", 0) > (row.get("sma20") or float("inf")), (row.get("sma20") or 0) > (row.get("sma50") or float("inf")), (row.get("sma50") or 0) > (row.get("sma200") or float("inf"))]
    values = {"trend": sum(checks) / 3,
              "momentum": (float(50 <= (row.get("rsi") or 0) <= 72) + float((row.get("macd_hist") or 0) > 0)) / 2,
              "volume": min(1, max(0, ((row.get("volume_ratio") or 0) - .5) / 1.5)),
              "strength": min(1, max(0, (relative_strength + .1) / .2)) if relative_strength is not None else 0,
              "structure": min(1, len(setups) / 2)}
    components = {k: round(values[k] * weight, 4) for k, weight in rules.weights.items()}
    score = round(sum(components.values()), 4)
    if score < rules.min_score:
        exclusions.append("Score below minimum")
    fmt = lambda key: f"{row[key]:.2f}" if row.get(key) is not None else "unavailable"
    explanation = [f"Price {row['close']:.4f}; SMA20/50/200 {fmt('sma20')}/{fmt('sma50')}/{fmt('sma200')}",
                   f"RSI {fmt('rsi')}; MACD histogram {fmt('macd_hist')}; relative volume {fmt('volume_ratio')}x",
                   f"63-session excess price return vs benchmark: {relative_strength:.2%}" if relative_strength is not None else "Relative strength unavailable: benchmark data missing",
                   "Weighted rule score; not probability or AI confidence. News does not change the score."]
    return {"symbol": instrument.symbol, "name": instrument.name, "market": instrument.market,
            "currency": "AUD" if instrument.market == "ASX" else "USD", "score": score, "score_components": components,
            "weights": rules.weights, "setups": setups, "qualified": not exclusions, "exclusions": exclusions,
            "indicators": row, "relative_strength": relative_strength, "market_cap": market_cap,
            "plan": trade_plan(row, params) if row.get("atr") else None, "explanation": explanation, "rule_version": RULE_VERSION}
