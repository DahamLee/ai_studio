"""점수 이후의 독립 단계. 각 함수는 한 가지 변환만 한다."""

from ai_studio.n01_frames import (
    ConditionFrame,
    IndicatorFrame,
    ScoreFrame,
    ScoreRow,
    SelectionFrame,
    SelectionRow,
    TargetPortfolio,
    TargetPosition,
    WeightFrame,
    WeightRow,
)


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



def score_from_indicator(indicators: IndicatorFrame, direction: str = "higher", method: str = "raw") -> ScoreFrame:
    """지표를 '높을수록 선호' 점수로 바꾼다. raw는 값 그대로(낮을수록이면 부호 반전),
    rank는 0~1 백분위, zscore는 표준점수. raw_value에 원래 지표 값을 남긴다."""
    if direction not in ("higher", "lower"):
        raise ValueError(f"지원하지 않는 방향입니다: {direction}")
    sign = 1 if direction == "higher" else -1
    values = [(row.instrument_id, row.value) for row in indicators.rows]
    if method == "raw":
        scored = [(symbol, sign * value) for symbol, value in values]
    elif method == "rank":
        ordered = sorted(values, key=lambda item: (sign * item[1], item[0]))
        count = len(ordered)
        position = {symbol: index for index, (symbol, _) in enumerate(ordered)}
        scored = [(symbol, position[symbol] / (count - 1) if count > 1 else 1.0) for symbol, _ in values]
    elif method == "zscore":
        if len(values) < 2:
            scored = [(symbol, 0.0) for symbol, _ in values]
        else:
            mean = sum(value for _, value in values) / len(values)
            spread = (sum((value - mean) ** 2 for _, value in values) / (len(values) - 1)) ** 0.5
            scored = [(symbol, sign * (value - mean) / spread if spread else 0.0) for symbol, value in values]
    else:
        raise ValueError(f"지원하지 않는 점수화 방식입니다: {method}")
    raw = dict(values)
    return ScoreFrame(tuple(ScoreRow(symbol, score, raw_value=raw[symbol]) for symbol, score in scored))


def combine_scores(left: ScoreFrame, right: ScoreFrame, left_weight: float) -> ScoreFrame:
    """두 점수의 가중합. 양쪽에 모두 점수가 있는 종목만 남긴다. 단위가 다르면 먼저 rank·zscore로 맞춘다."""
    if not 0 <= left_weight <= 1:
        raise ValueError("왼쪽 점수 비율은 0과 1 사이여야 합니다.")
    rights = {row.instrument_id: row.score for row in right.rows}
    return ScoreFrame(tuple(
        ScoreRow(row.instrument_id, left_weight * row.score + (1 - left_weight) * rights[row.instrument_id])
        for row in left.rows if row.instrument_id in rights
    ))


def select_top_n_with_buffer(scores: ScoreFrame, top_n: int, held: set[str], buffer: int) -> SelectionFrame:
    """보유 종목은 순위가 top_n + buffer 안이면 남기고, 남는 자리를 순위대로 채운다."""
    if top_n < 1 or buffer < 0:
        raise ValueError("상위 종목 수는 1 이상, 버퍼는 0 이상이어야 합니다.")
    ordered = sorted(scores.rows, key=lambda row: (-row.score, row.instrument_id))
    kept = [row for row in ordered[:top_n + buffer] if row.instrument_id in held][:top_n]
    kept_ids = {row.instrument_id for row in kept}
    fill = [row for row in ordered if row.instrument_id not in kept_ids][:top_n - len(kept)]
    chosen = sorted(kept + fill, key=lambda row: (-row.score, row.instrument_id))
    return SelectionFrame(tuple(
        SelectionRow(row.instrument_id, rank, row.score) for rank, row in enumerate(chosen, start=1)
    ))


def select_matched(condition: ConditionFrame) -> SelectionFrame:
    """조건이 참인 종목을 선정한다. 점수가 없으므로 순위는 종목 id 순이다."""
    chosen = sorted(row.instrument_id for row in condition.rows if row.matched)
    return SelectionFrame(tuple(SelectionRow(symbol, rank) for rank, symbol in enumerate(chosen, start=1)))


def weight_by_indicator(selection: SelectionFrame, indicators: IndicatorFrame, direction: str = "inverse") -> WeightFrame:
    """지표에 비례(proportional) 또는 반비례(inverse)해 비중을 나눈다. 역변동성 배분에 쓴다."""
    if direction not in ("inverse", "proportional"):
        raise ValueError(f"지원하지 않는 방향입니다: {direction}")
    values = {row.instrument_id: row.value for row in indicators.rows}
    missing = [row.instrument_id for row in selection.rows if row.instrument_id not in values]
    if missing:
        raise ValueError(f"지표 값이 없는 선정 종목: {missing[:5]}")
    raw = {}
    for row in selection.rows:
        value = values[row.instrument_id]
        if value <= 0:
            raise ValueError(f"{row.instrument_id}: 지표 비례 비중은 양수 지표만 받을 수 있습니다.")
        raw[row.instrument_id] = 1 / value if direction == "inverse" else value
    total = sum(raw.values())
    return WeightFrame(tuple(WeightRow(symbol, value / total) for symbol, value in raw.items())) if total else WeightFrame(())


def scale_when_false(weights: WeightFrame, condition: ConditionFrame, keep_weight: float) -> WeightFrame:
    """조건이 거짓인 종목 비중에 keep_weight를 곱한다. 줄어든 만큼은 현금이 된다.
    조건이 시장 한 줄(MARKET)이면 모든 종목에 같은 판단을 쓴다. 조건 행이 없는 종목은 거짓으로 본다."""
    if not 0 <= keep_weight <= 1:
        raise ValueError("유지 비율은 0과 1 사이여야 합니다.")
    flags = condition.matches()
    market = flags.get("MARKET") if list(flags) == ["MARKET"] else None
    return WeightFrame(tuple(
        WeightRow(row.instrument_id, row.weight if (market if market is not None else flags.get(row.instrument_id, False))
                  else row.weight * keep_weight)
        for row in weights.rows
    ))
