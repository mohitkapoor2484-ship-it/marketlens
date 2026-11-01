import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UTC = timezone.utc
RULE_VERSION = "eod-rules-1.0"


def now_utc():
    return datetime.now(UTC)


def stamp(value=None):
    return (value or now_utc()).astimezone(UTC).isoformat()


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return dt.astimezone(UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Instrument(StrictModel):
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.\-]{0,19}\.(AU|US)$")
    name: str = Field(min_length=1, max_length=150)
    market: Literal["ASX", "US"]
    benchmark: str
    role: Literal["stock", "etf", "benchmark"] = "stock"
    member_from: str | None = None
    member_to: str | None = None
    membership_evidence: str | None = None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("Display name cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def suffix(self):
        if not self.symbol.endswith(".AU" if self.market == "ASX" else ".US"):
            raise ValueError("Market and provider symbol suffix disagree")
        return self


class Bar(StrictModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    adjusted_close: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def ohlc(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("Invalid OHLC envelope")
        return self


class Costs(StrictModel):
    commission_bps: float = Field(8, ge=0, le=500)
    minimum_fee_aud: float = Field(3, ge=0, le=100)
    spread_bps: float = Field(10, ge=0, le=1000)
    slippage_bps: float = Field(5, ge=0, le=1000)
    fx_fee_bps: float = Field(30, ge=0, le=1000)
    participation_rate: float = Field(0.01, gt=0, le=0.1)


class PortfolioRules(StrictModel):
    starting_balance: float = Field(10000, ge=100, le=1e9)
    risk_per_trade: float = Field(0.01, gt=0, le=0.05)
    max_position_fraction: float = Field(0.2, gt=0, le=1)
    max_positions: int = Field(5, ge=1, le=50)
    max_portfolio_risk: float = Field(0.05, gt=0, le=0.25)
    expiry_sessions: int = Field(3, ge=1, le=20)


class ScanRules(StrictModel):
    min_price: float = Field(1, ge=0)
    min_dollar_volume: float = Field(1_000_000, ge=0)
    min_market_cap: float = Field(0, ge=0)
    min_atr_pct: float = Field(0.25, ge=0)
    max_atr_pct: float = Field(10, gt=0)
    min_score: float = Field(50, ge=0, le=100)
    weights: dict[str, float] = Field(default_factory=lambda: {"trend": 30, "momentum": 20, "volume": 20, "strength": 15, "structure": 15})

    @model_validator(mode="after")
    def weights_valid(self):
        if set(self.weights) != {"trend", "momentum", "volume", "strength", "structure"}:
            raise ValueError("Exactly five documented score weights required")
        if not all(math.isfinite(v) and v >= 0 for v in self.weights.values()) or abs(sum(self.weights.values()) - 100) > 1e-6:
            raise ValueError("Weights must be nonnegative and sum to 100")
        if self.max_atr_pct < self.min_atr_pct:
            raise ValueError("ATR bounds reversed")
        return self


class StrategyParams(StrictModel):
    strategy: Literal["breakout", "momentum_continuation", "moving_average_pullback", "volume_breakout", "trend_reversal_candidate", "consolidation"] = "breakout"
    lookback: int = Field(20, ge=10, le=100)
    stop_atr: float = Field(2, ge=0.5, le=5)
    target_r: float = Field(2, ge=1, le=10)


class AppRules(StrictModel):
    portfolio: PortfolioRules = Field(default_factory=PortfolioRules)
    costs: Costs = Field(default_factory=Costs)
    scanner: ScanRules = Field(default_factory=ScanRules)
    strategy: StrategyParams = Field(default_factory=StrategyParams)
