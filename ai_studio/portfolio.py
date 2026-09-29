"""점수 이후의 독립 단계. 각 함수는 한 가지 변환만 한다."""

from ai_studio.frames import (
    IndicatorFrame,
    ScoreFrame,
    SelectionFrame,
    SelectionRow,
    TargetPortfolio,
    TargetPosition,
    WeightFrame,
    WeightRow,
)

_OPERATORS = {
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
}


def select_top_n(scores: ScoreFrame, top_n: int) -> SelectionFrame:
    if top_n < 1:
        raise ValueError("상위 종목 수는 1 이상이어야 합니다.")
    ordered = sorted(scores.rows, key=lambda row: (-row.score, row.instrument_id))
    chosen = ordered[:top_n]
    return SelectionFrame(tuple(
        SelectionRow(row.instrument_id, rank, row.score)
        for rank, row in enumerate(chosen, start=1)
    ))


def select_min_score(scores: ScoreFrame, min_score: float) -> SelectionFrame:
    chosen = sorted(
        (row for row in scores.rows if row.score >= min_score),
        key=lambda row: (-row.score, row.instrument_id),
    )
    return SelectionFrame(tuple(
        SelectionRow(row.instrument_id, rank, row.score)
        for rank, row in enumerate(chosen, start=1)
    ))


def weight_equal(selection: SelectionFrame) -> WeightFrame:
    count = len(selection.rows)
    if count == 0:
        return WeightFrame(())
    weight = 1 / count
    return WeightFrame(tuple(WeightRow(row.instrument_id, weight) for row in selection.rows))


def weight_by_score(selection: SelectionFrame) -> WeightFrame:
    if any(row.score is None for row in selection.rows):
        raise ValueError("점수 비례 비중에는 선정 종목의 점수가 필요합니다.")
    scores = [row.score for row in selection.rows if row.score is not None]
    if any(score <= 0 for score in scores):
        raise ValueError("점수 비례 비중은 양수 점수만 받을 수 있습니다.")
    total = sum(scores)
    if total == 0:
        raise ValueError("점수 합계가 0이면 비중을 나눌 수 없습니다.")
    return WeightFrame(tuple(
        WeightRow(row.instrument_id, row.score / total)
        for row in selection.rows
        if row.score is not None
    ))


def cap_max_weight(weights: WeightFrame, max_weight: float) -> WeightFrame:
    if not 0 <= max_weight <= 1:
        raise ValueError("종목 상한은 0과 1 사이여야 합니다.")
    return WeightFrame(tuple(
        WeightRow(row.instrument_id, min(row.weight, max_weight))
        for row in weights.rows
    ))


def apply_cash_weight(weights: WeightFrame, cash_weight: float) -> WeightFrame:
    if not 0 <= cash_weight <= 1:
        raise ValueError("현금 비중은 0과 1 사이여야 합니다.")
    total = sum(row.weight for row in weights.rows)
    investable = 1 - cash_weight
    if total == 0 or investable == 0:
        return WeightFrame(tuple(WeightRow(row.instrument_id, 0.0) for row in weights.rows))
    scale = investable / total
    return WeightFrame(tuple(WeightRow(row.instrument_id, row.weight * scale) for row in weights.rows))


def to_target_portfolio(weights: WeightFrame) -> TargetPortfolio:
    positions = tuple(
        TargetPosition(row.instrument_id, row.weight)
        for row in weights.rows
        if row.weight > 0
    )
    cash = 1 - sum(row.target_weight for row in positions)
    return TargetPortfolio(positions, cash)


def compare_threshold(indicators: IndicatorFrame, operator: str, threshold: float) -> dict[str, bool]:
    try:
        predicate = _OPERATORS[operator]
    except KeyError as error:
        raise ValueError(f"지원하지 않는 비교입니다: {operator}") from error
    return {row.instrument_id: predicate(row.value, threshold) for row in indicators.rows}


def rsi(closes: list[float], period: int = 14) -> float:
    if period < 2:
        raise ValueError("RSI 기간은 2 이상이어야 합니다.")
    if len(closes) < period + 1:
        raise ValueError("RSI를 계산할 가격이 부족합니다.")
    gains = 0.0
    losses = 0.0
    for index in range(-period, 0):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    if losses == 0:
        return 100.0
    relative = (gains / period) / (losses / period)
    return 100 - (100 / (1 + relative))
