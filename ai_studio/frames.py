"""점수부터 목표 포트폴리오까지의 공통 입출력.

score가 높을수록 더 선호되는 종목이다. 이 모듈의 결과는 주문 수량을 포함하지 않는다.
"""

from dataclasses import dataclass


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
