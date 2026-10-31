import math
from datetime import date

import numpy as np


def wilson(wins, n):
    if n == 0:
        return None
    z = 1.96
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0, center - half), min(1, center + half)]


def metrics(curve, trades, starting_balance):
    end = curve[-1]["equity"] if curve else starting_balance
    n = len(trades)
    wins = sum(t["pnl_aud"] > 0 for t in trades)
    profit = sum(max(0, t["pnl_aud"]) for t in trades)
    loss = -sum(min(0, t["pnl_aud"]) for t in trades)
    values = np.array([starting_balance] + [r["equity"] for r in curve], dtype=float)
    rets = np.diff(values) / values[:-1] if len(values) > 1 else np.array([])
    peaks = np.maximum.accumulate(values)
    dd = values / peaks - 1
    elapsed = (date.fromisoformat(curve[-1]["date"][:10]) - date.fromisoformat(curve[0]["date"][:10])).days if len(curve) > 1 else 0
    r_values = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    return {"total_return": end / starting_balance - 1, "annualised_return": (end / starting_balance) ** (365.25 / elapsed) - 1 if elapsed >= 365 and end > 0 else None,
            "win_rate": wins / n if n else None, "win_rate_95_interval": wilson(wins, n), "trade_count": n,
            "profit_factor": profit / loss if loss > 0 else None, "gross_profit": profit, "gross_loss": loss,
            "average_r": float(np.mean(r_values)) if r_values else None, "expectancy_aud": sum(t["pnl_aud"] for t in trades) / n if n else None,
            "sharpe": float(np.mean(rets) / np.std(rets, ddof=1) * math.sqrt(252)) if len(rets) >= 30 and np.std(rets, ddof=1) > 1e-12 else None,
            "max_drawdown": float(np.min(dd)), "exposure": float(np.mean([r["exposure"] for r in curve])) if curve else 0,
            "end_equity": end, "sample_warning": "100 signals is a monitoring milestone, not proof of significance. Dependence and selection bias are not captured by the descriptive Wilson interval.",
            "metric_assumptions": "Daily sampled arithmetic returns; Sharpe uses 252 sessions and zero risk-free rate. Annualisation only after ≥365 elapsed days. Profit factor null when no losses; win rate uses closed trades only."}
