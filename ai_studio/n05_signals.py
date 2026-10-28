"""지표를 지속 조건으로, 조건의 변화를 진입·청산 이벤트로 바꾼다.

조건 함수는 받은 지표 값만 비교한다. 지표 계산은 n08의 지표 블록이 맡는다.
"""

from datetime import datetime

from ai_studio.n01_frames import ConditionFrame, ConditionRow, EventFrame, EventRow, IndicatorFrame

OPERATORS = {
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
}


def compare_threshold(indicators: IndicatorFrame, operator: str, threshold: float) -> ConditionFrame:
    """지표가 임계값을 만족하면 참. 만족하지 않은 종목도 거짓 행으로 남긴다."""
    try:
        predicate = OPERATORS[operator]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 비교입니다: {operator}") from error
    return ConditionFrame(tuple(
        ConditionRow(row.instrument_id, predicate(row.value, threshold)) for row in indicators.rows
    ))


MARKET_ID = "MARKET"


def compare_indicators(
    left: IndicatorFrame, right: IndicatorFrame, operator: str, offset_pct: float = 0,
) -> ConditionFrame:
    """left (연산자) right × (1 + offset%). 한쪽이 시장 한 줄이면 다른 쪽 모든 종목에 펼친다.
    양쪽에 다 있는 종목만 행이 된다."""
    try:
        predicate = OPERATORS[operator]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 비교입니다: {operator}") from error
    lefts = {row.instrument_id: row.value for row in left.rows}
    rights = {row.instrument_id: row.value for row in right.rows}
    left_ids = list(lefts)
    right_ids = list(rights)
    if left_ids == [MARKET_ID] and right_ids != [MARKET_ID]:
        ids = right_ids
    elif right_ids == [MARKET_ID] and left_ids != [MARKET_ID]:
        ids = left_ids
    else:
        ids = [symbol for symbol in left_ids if symbol in rights]
    factor = 1 + offset_pct / 100
    rows = []
    for symbol in ids:
        lvalue = lefts.get(symbol, lefts.get(MARKET_ID))
        rvalue = rights.get(symbol, rights.get(MARKET_ID))
        rows.append(ConditionRow(symbol, predicate(lvalue, rvalue * factor)))
    return ConditionFrame(tuple(rows))


def negate(condition: ConditionFrame) -> ConditionFrame:
    return ConditionFrame(tuple(ConditionRow(row.instrument_id, not row.matched) for row in condition.rows))


def transition_events(
    previous: ConditionFrame | None, current: ConditionFrame, as_of: datetime,
) -> tuple[EventFrame, EventFrame]:
    """이전 평가에서 거짓→지금 참이면 진입, 참→거짓이면 청산. 이전 값이 없는 종목은 이벤트가 없다."""
    if previous is None:
        return EventFrame(()), EventFrame(())
    before = previous.matches()
    entries: list[EventRow] = []
    exits: list[EventRow] = []
    for row in current.rows:
        was = before.get(row.instrument_id)
        if was is False and row.matched:
            entries.append(EventRow(row.instrument_id, as_of, "entry"))
        elif was is True and not row.matched:
            exits.append(EventRow(row.instrument_id, as_of, "exit"))
    return EventFrame(tuple(entries)), EventFrame(tuple(exits))
