"""블록 사이로 오가는 공통 입출력과 기본 타입.

score가 높을수록 더 선호되는 종목이다. 이 모듈의 결과는 주문 수량을 포함하지 않는다.
조건은 유니버스의 모든 종목에 참·거짓이 남고, 이벤트는 발생한 시점만 담는다.
시각은 모두 시간대가 있는 datetime이다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Literal


class StrategyValidationError(ValueError):
    pass


def require_tz(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyValidationError(f"{name}: 타임존이 있는 시각이 필요합니다.")


@dataclass(frozen=True, slots=True)
class MarketBar:
    """한 봉의 시세. timestamp는 봉이 끝나는 시각, available_at은 실제로 쓸 수 있게 된 시각,
    open_at은 그 봉의 시가 시각이다. available_at을 비우면 timestamp와 같다."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    available_at: datetime | None = field(default=None, kw_only=True)
    open_at: datetime | None = field(default=None, kw_only=True)
    frequency: str = field(default="1d", kw_only=True)

    def __post_init__(self) -> None:
        require_tz(self.timestamp, "bar.timestamp")
        if self.available_at is None:
            object.__setattr__(self, "available_at", self.timestamp)
        require_tz(self.available_at, "bar.available_at")
        if self.available_at < self.timestamp:
            raise StrategyValidationError(f"{self.symbol} {self.timestamp}: 공개시점이 데이터 시점보다 빠릅니다.")
        if self.open_at is not None:
            require_tz(self.open_at, "bar.open_at")
            if self.open_at > self.timestamp:
                raise StrategyValidationError(f"{self.symbol}: 봉의 시가가 종가 이후일 수 없습니다.")
        prices = (self.open, self.high, self.low, self.close)
        if not self.symbol or not all(isfinite(p) and p > 0 for p in prices) or self.low > self.high:
            raise StrategyValidationError(f"bar {self.symbol} {self.timestamp}: 가격이 유효해야 합니다.")
        if not isfinite(self.volume) or self.volume < 0:
            raise StrategyValidationError(f"bar {self.symbol} {self.timestamp}: 거래량이 유효해야 합니다.")


@dataclass(frozen=True, slots=True)
class UniverseFrame:
    as_of: datetime
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        require_tz(self.as_of, "universe.as_of")
        if len(set(self.symbols)) != len(self.symbols) or any(not s for s in self.symbols):
            raise StrategyValidationError("universe: 종목이 중복되거나 비어 있습니다.")


@dataclass(frozen=True, slots=True)
class ScoreRow:
    instrument_id: str
    score: float
    raw_value: float | None = None
    rank: int | None = None
    eligible: bool | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class ScoreFrame:
    rows: tuple[ScoreRow, ...]

    def __post_init__(self) -> None:
        _unique_ids(self.rows)


@dataclass(frozen=True, slots=True)
class IndicatorRow:
    instrument_id: str
    value: float


@dataclass(frozen=True, slots=True)
class IndicatorFrame:
    rows: tuple[IndicatorRow, ...]

    def __post_init__(self) -> None:
        _unique_ids(self.rows)


@dataclass(frozen=True, slots=True)
class ConditionRow:
    instrument_id: str
    matched: bool


@dataclass(frozen=True, slots=True)
class ConditionFrame:
    """조건의 현재 상태. 거짓인 종목도 행으로 남는다."""

    rows: tuple[ConditionRow, ...]

    def __post_init__(self) -> None:
        _unique_ids(self.rows)

    def matches(self) -> dict[str, bool]:
        return {row.instrument_id: row.matched for row in self.rows}


@dataclass(frozen=True, slots=True)
class EventRow:
    instrument_id: str
    as_of: datetime
    kind: Literal["entry", "exit"]


@dataclass(frozen=True, slots=True)
class EventFrame:
    """진입 또는 청산이 발생한 기록. 발생하지 않은 종목은 행이 없다."""

    rows: tuple[EventRow, ...]

    def __post_init__(self) -> None:
        seen: set[tuple[str, datetime, str]] = set()
        for row in self.rows:
            if not row.instrument_id:
                raise StrategyValidationError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
            if row.kind not in ("entry", "exit"):
                raise StrategyValidationError("이벤트 종류는 진입 또는 청산이어야 합니다.")
            require_tz(row.as_of, "event.as_of")
            key = (row.instrument_id, row.as_of, row.kind)
            if key in seen:
                raise StrategyValidationError("같은 시각의 같은 이벤트는 한 번만 있어야 합니다.")
            seen.add(key)

    def of_kind(self, kind: Literal["entry", "exit"]) -> "EventFrame":
        return EventFrame(tuple(row for row in self.rows if row.kind == kind))


@dataclass(frozen=True, slots=True)
class SelectionRow:
    instrument_id: str
    rank: int
    score: float | None = None


@dataclass(frozen=True, slots=True)
class SelectionFrame:
    rows: tuple[SelectionRow, ...]

    def __post_init__(self) -> None:
        _unique_ids(self.rows)
        ranks = [row.rank for row in self.rows]
        if any(rank < 1 for rank in ranks) or len(ranks) != len(set(ranks)):
            raise StrategyValidationError("선정 순위는 1부터 시작하는 고유 값이어야 합니다.")


@dataclass(frozen=True, slots=True)
class WeightRow:
    instrument_id: str
    weight: float


@dataclass(frozen=True, slots=True)
class WeightFrame:
    rows: tuple[WeightRow, ...]

    def __post_init__(self) -> None:
        _unique_ids(self.rows)
        for row in self.rows:
            if row.weight < 0:
                raise StrategyValidationError("비중은 음수일 수 없습니다.")
        if sum(row.weight for row in self.rows) > 1 + 1e-9:
            raise StrategyValidationError("종목 비중 합계는 1을 넘을 수 없습니다.")


@dataclass(frozen=True, slots=True)
class TargetPosition:
    instrument_id: str
    target_weight: float


@dataclass(frozen=True, slots=True)
class TargetPortfolio:
    positions: tuple[TargetPosition, ...]
    cash_weight: float

    def __post_init__(self) -> None:
        ids = [row.instrument_id for row in self.positions]
        if len(ids) != len(set(ids)) or any(not item for item in ids):
            raise StrategyValidationError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
        if self.cash_weight < -1e-9:
            raise StrategyValidationError("현금 비중은 음수일 수 없습니다.")
        total = self.cash_weight + sum(row.target_weight for row in self.positions)
        if abs(total - 1) > 1e-9:
            raise StrategyValidationError("목표 비중과 현금 비중의 합은 1이어야 합니다.")


def _unique_ids(rows: tuple[object, ...]) -> None:
    ids = [getattr(row, "instrument_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise StrategyValidationError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
