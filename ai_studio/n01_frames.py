"""블록 사이로 오가는 공통 입출력.

score가 높을수록 더 선호되는 종목이다. 이 모듈의 결과는 주문 수량을 포함하지 않는다.
조건은 유니버스의 모든 종목에 참·거짓이 남고, 이벤트는 발생한 시점만 담는다.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class UniverseRow:
    instrument_id: str
    eligible: bool = True
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class UniverseFrame:
    as_of: str
    rows: tuple[UniverseRow, ...]

    def __post_init__(self) -> None:
        if not self.as_of:
            raise ValueError("universe.as_of가 비어 있습니다.")
        _unique_ids(self.rows)

    def symbols(self) -> tuple[str, ...]:
        return tuple(row.instrument_id for row in self.rows if row.eligible)


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
                raise ValueError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
            if row.kind not in ("entry", "exit"):
                raise ValueError("이벤트 종류는 진입 또는 청산이어야 합니다.")
            if row.as_of.tzinfo is None:
                raise ValueError("이벤트 시각은 시간대가 있어야 합니다.")
            key = (row.instrument_id, row.as_of, row.kind)
            if key in seen:
                raise ValueError("같은 시각의 같은 이벤트는 한 번만 있어야 합니다.")
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
            raise ValueError("선정 순위는 1부터 시작하는 고유 값이어야 합니다.")


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
                raise ValueError("비중은 음수일 수 없습니다.")
        if sum(row.weight for row in self.rows) > 1 + 1e-9:
            raise ValueError("종목 비중 합계는 1을 넘을 수 없습니다.")


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
            raise ValueError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
        if self.cash_weight < -1e-9:
            raise ValueError("현금 비중은 음수일 수 없습니다.")
        total = self.cash_weight + sum(row.target_weight for row in self.positions)
        if abs(total - 1) > 1e-9:
            raise ValueError("목표 비중과 현금 비중의 합은 1이어야 합니다.")


def _unique_ids(rows: tuple[object, ...]) -> None:
    ids = [getattr(row, "instrument_id") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("instrument_id는 비어 있지 않은 고유 값이어야 합니다.")
