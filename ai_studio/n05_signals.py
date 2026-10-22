"""지표나 가격을 지속 조건 또는 진입·청산 이벤트로 바꾼다.

일반 조건 함수는 받은 지표 값을 비교한다. 이동평균 교차 함수는 MarketBar에서
단기·장기 평균을 계산하고, 교차한 시점의 전이만 담는다.
"""

from collections.abc import Sequence
from datetime import datetime

from ai_studio.n01_frames import (
    ConditionFrame, ConditionRow, EventFrame, EventRow, IndicatorFrame, MarketBar, StrategyValidationError,
)
from ai_studio.n04_indicators import moving_average

_OPERATORS = {
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
}


def compare_threshold(indicators: IndicatorFrame, operator: str, threshold: float) -> ConditionFrame:
    """지표가 임계값을 만족하면 참. 만족하지 않은 종목도 거짓 행으로 남긴다."""
    try:
        predicate = _OPERATORS[operator]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 비교입니다: {operator}") from error
    return ConditionFrame(tuple(
        ConditionRow(row.instrument_id, predicate(row.value, threshold)) for row in indicators.rows
    ))


def price_above_moving_average(price: float, average: float) -> bool:
    return price > average


def fast_average_above_slow(fast: float, slow: float) -> bool:
    return fast > slow


def crossed_above(previous_fast: float, previous_slow: float, current_fast: float, current_slow: float) -> bool:
    return previous_fast <= previous_slow and current_fast > current_slow


def crossed_below(previous_fast: float, previous_slow: float, current_fast: float, current_slow: float) -> bool:
    return previous_fast >= previous_slow and current_fast < current_slow


def breaks_prior_high(close: float, prior_high: float, buffer_pct: float = 0) -> bool:
    return close >= prior_high * (1 + buffer_pct / 100)


def moving_average_cross_events(
    bars: Sequence[MarketBar], fast_days: int, slow_days: int
) -> EventFrame:
    """단기·장기 이동평균이 교차한 날에만 진입 또는 청산 이벤트를 낸다."""
    if fast_days < 1 or slow_days <= fast_days:
        raise StrategyValidationError("moving_average_cross_events: 0 < fast_days < slow_days가 필요합니다.")
    groups: dict[str, list[MarketBar]] = {}
    seen: set[tuple[str, datetime]] = set()
    for bar in bars:
        key = (bar.symbol, bar.timestamp)
        if key in seen:
            raise StrategyValidationError(f"중복 시계열: {bar.symbol} {bar.timestamp}")
        seen.add(key)
        groups.setdefault(bar.symbol, []).append(bar)
    output: list[EventRow] = []
    for symbol, series in groups.items():
        series.sort(key=lambda bar: bar.timestamp)
        if len({bar.frequency for bar in series}) != 1:
            raise StrategyValidationError(f"{symbol}: 서로 다른 가격 주기가 섞여 있습니다.")
        close = [bar.close for bar in series]
        for index in range(slow_days, len(series)):
            if any(bar.available_at > series[index].available_at for bar in series[index - slow_days:index + 1]):
                raise StrategyValidationError(f"{symbol} {series[index].timestamp}: 미래 공개 데이터가 포함됐습니다.")
            previous_fast = moving_average(close[:index], fast_days)
            previous_slow = moving_average(close[:index], slow_days)
            current_fast = moving_average(close[:index + 1], fast_days)
            current_slow = moving_average(close[:index + 1], slow_days)
            entry = crossed_above(previous_fast, previous_slow, current_fast, current_slow)
            exit_ = crossed_below(previous_fast, previous_slow, current_fast, current_slow)
            if entry:
                output.append(EventRow(symbol, series[index].available_at, "entry"))
            elif exit_:
                output.append(EventRow(symbol, series[index].available_at, "exit"))
    return EventFrame(tuple(sorted(output, key=lambda row: (row.as_of, row.instrument_id, row.kind))))
