"""화면 블록의 계산.

`n10_functions`의 블록 id와 대응한다. 점수가 높을수록 더 선호되는 종목이다.
가치·퀄리티 점수는 재무 항목이 필요해서 이 모듈에 없다.
개월 수는 거래일 21일로 센다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import sqrt

from ai_studio.n01_frames import (
    EventFrame,
    MarketBar,
    IndicatorFrame,
    IndicatorRow,
    ScoreFrame,
    ScoreRow,
    SelectionFrame,
    ConditionFrame,
    ConditionRow,
    UniverseFrame,
    WeightFrame,
    WeightRow,
)
from ai_studio.n03_market_data import History, bars_until, session_date
from ai_studio.n04_indicators import moving_average, rsi, window_high
from ai_studio.n06_portfolio import weight_by_score, weight_equal
from ai_studio.n05_signals import (
    breaks_prior_high,
    fast_average_above_slow,
    moving_average_cross_events,
    price_above_moving_average,
)

TRADING_DAYS_PER_MONTH = 21


def compose_universe(history: History, as_of: datetime) -> UniverseFrame:
    """universe.compose. 적재된 종목 전체를 같은 유니버스 프레임으로 반환한다."""
    symbols = tuple(sorted(symbol for symbol, rows in history.items() if rows))
    return _universe(as_of, symbols)


def exclude_universe(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    min_listing_days: int = 252,
    min_average_turnover: float = 0,
    turnover_window: int = 20,
    exclude_halted: bool = True,
    exclude_financials: bool = False,
    exclude_loss_making: bool = False,
) -> UniverseFrame:
    """universe.exclude. 거래대금은 종가×거래량이고 통화는 일봉과 같다."""
    if min_listing_days < 0 or min_average_turnover < 0 or turnover_window < 1:
        raise ValueError("제외 조건의 기간과 거래대금은 0 이상이어야 합니다.")
    if exclude_financials or exclude_loss_making:
        raise ValueError("금융주·적자 제외는 업종과 순이익이 필요합니다. 일봉만으로는 계산하지 않습니다.")
    kept: list[str] = []
    for symbol in symbols:
        series = _upto(history, symbol, as_of)
        if exclude_halted and (not series or session_date(series[-1].timestamp) != session_date(as_of)):
            continue
        if len(series) < min_listing_days:
            continue
        window = series[-turnover_window:]
        average = sum(bar.close * bar.volume for bar in window) / len(window)
        if average < min_average_turnover:
            continue
        kept.append(symbol)
    return _universe(as_of, kept)


def min_price_universe(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    min_close: float = 0,
) -> UniverseFrame:
    """universe.min-price. 기준일 종가가 하한보다 낮으면 뺀다. 0이면 그대로 통과시킨다."""
    if min_close < 0:
        raise ValueError("최소 가격은 0 이상이어야 합니다.")
    if min_close == 0:
        return _universe(as_of, symbols)
    kept: list[str] = []
    for symbol in symbols:
        series = _upto(history, symbol, as_of)
        if series and session_date(series[-1].timestamp) == session_date(as_of) and series[-1].close >= min_close:
            kept.append(symbol)
    return _universe(as_of, kept)


def _universe(as_of: datetime, symbols: Sequence[str]) -> UniverseFrame:
    return UniverseFrame(as_of, tuple(symbols))


def _condition(matches: Mapping[str, bool]) -> ConditionFrame:
    return ConditionFrame(tuple(ConditionRow(symbol, matched) for symbol, matched in matches.items()))


def momentum_score(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    lookback_months: int = 12,
    skip_months: int = 1,
) -> ScoreFrame:
    """momentum.factor. 최근 skip 개월을 뺀 과거 lookback 개월 수익률."""
    if lookback_months < 1 or skip_months < 0:
        raise ValueError("모멘텀 기간은 1개월 이상, 제외 기간은 0개월 이상이어야 합니다.")
    lookback = lookback_months * TRADING_DAYS_PER_MONTH
    skip = skip_months * TRADING_DAYS_PER_MONTH
    rows: list[ScoreRow] = []
    for symbol in symbols:
        series = _upto(history, symbol, as_of)
        end = len(series) - 1 - skip
        start = end - lookback
        if start < 0 or end < 0 or series[start].close <= 0:
            continue
        raw = series[end].close / series[start].close - 1
        rows.append(ScoreRow(symbol, raw, raw_value=raw))
    return ScoreFrame(tuple(rows))


def trend_condition(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    method: str = "price_above_ma",
    fast_days: int = 20,
    slow_days: int = 60,
) -> ConditionFrame:
    """momentum.trend. 가격이 부족하면 거짓이다."""
    if fast_days < 1 or slow_days < 2:
        raise ValueError("이동평균 기간이 너무 짧습니다.")
    if method == "ma_cross" and fast_days >= slow_days:
        raise ValueError("단기 이동평균은 장기보다 짧아야 합니다.")
    if method not in ("price_above_ma", "ma_cross"):
        raise ValueError(f"지원하지 않는 추세 판단입니다: {method}")
    output: dict[str, bool] = {}
    for symbol in symbols:
        closes = [bar.close for bar in _upto(history, symbol, as_of)]
        if len(closes) < slow_days:
            output[symbol] = False
            continue
        slow = moving_average(closes, slow_days)
        if method == "price_above_ma":
            output[symbol] = price_above_moving_average(closes[-1], slow)
        else:
            fast = moving_average(closes, fast_days)
            output[symbol] = fast_average_above_slow(fast, slow)
    return _condition(output)


def breakout_condition(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    window_days: int = 252,
    buffer_pct: float = 0,
) -> ConditionFrame:
    """momentum.breakout. 오늘 종가가 이전 고점보다 여유 비율 이상이면 참."""
    if window_days < 2 or buffer_pct < 0:
        raise ValueError("고점 기간은 2일 이상이고 돌파 여유는 0 이상이어야 합니다.")
    output: dict[str, bool] = {}
    for symbol in symbols:
        series = _upto(history, symbol, as_of)
        if len(series) < window_days + 1:
            output[symbol] = False
            continue
        highs = [bar.high for bar in series[:-1]]
        prior_high = window_high(highs, window_days)
        output[symbol] = breaks_prior_high(series[-1].close, prior_high, buffer_pct)
    return _condition(output)


def low_volatility_score(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    metric: str = "stdev",
    window_days: int = 252,
) -> ScoreFrame:
    """factor.low-volatility. 점수는 변동이 작을수록 높다. raw_value가 표준편차 또는 베타다."""
    if window_days < 2:
        raise ValueError("변동성 기간은 2일 이상이어야 합니다.")
    if metric == "stdev":
        rows = []
        for symbol in symbols:
            returns = _tail_returns(_upto(history, symbol, as_of), window_days)
            if len(returns) < window_days:
                continue
            deviation = _stdev(returns)
            rows.append(ScoreRow(symbol, -deviation, raw_value=deviation))
        return ScoreFrame(tuple(rows))
    if metric == "beta":
        return _beta_scores(history, symbols, as_of, window_days)
    raise ValueError(f"지원하지 않는 변동성 지표입니다: {metric}")


def rsi_indicator(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    period: int = 14,
) -> IndicatorFrame:
    """indicator.rsi. 가격이 부족하면 그 종목은 빠진다."""
    rows: list[IndicatorRow] = []
    for symbol in symbols:
        closes = [bar.close for bar in _upto(history, symbol, as_of)]
        if len(closes) < period + 1:
            continue
        rows.append(IndicatorRow(symbol, rsi(closes, period)))
    return IndicatorFrame(tuple(rows))


def moving_average_cross(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    fast_days: int = 20,
    slow_days: int = 60,
) -> EventFrame:
    """momentum.ma-cross. 교차한 날만 진입 또는 청산 이벤트가 된다. as_of 이후 봉은 보지 않는다."""
    bars = [bar for symbol in symbols for bar in bars_until(history, symbol, as_of)]
    return moving_average_cross_events(bars, fast_days, slow_days)


def combine_conditions(operator: str, *conditions: ConditionFrame) -> ConditionFrame:
    """logic.combine. 모든 조건은 같은 종목 순서여야 한다."""
    if operator not in ("and", "or") or len(conditions) < 2:
        raise ValueError("AND 또는 OR로 두 개 이상의 조건을 합쳐야 합니다.")
    symbols = [row.instrument_id for row in conditions[0].rows]
    if any([row.instrument_id for row in item.rows] != symbols for item in conditions[1:]):
        raise ValueError("결합할 조건의 종목 집합이 다릅니다.")
    flags = [item.matches() for item in conditions]
    if operator == "and":
        return _condition({symbol: all(item[symbol] for item in flags) for symbol in symbols})
    return _condition({symbol: any(item[symbol] for item in flags) for symbol in symbols})


def if_else_score(
    condition: ConditionFrame,
    then_scores: ScoreFrame,
    else_scores: ScoreFrame,
) -> ScoreFrame:
    """logic.if-else. 조건이 참이면 첫 점수, 거짓이면 둘째 점수를 고른다."""
    then_map = {row.instrument_id: row for row in then_scores.rows}
    else_map = {row.instrument_id: row for row in else_scores.rows}
    rows: list[ScoreRow] = []
    for row in condition.rows:
        chosen = (then_map if row.matched else else_map).get(row.instrument_id)
        if chosen is None:
            raise ValueError(f"{row.instrument_id}: 선택한 분기에 점수가 없습니다.")
        rows.append(ScoreRow(row.instrument_id, chosen.score, raw_value=chosen.raw_value))
    return ScoreFrame(tuple(rows))


def if_else_condition(
    condition: ConditionFrame,
    then_condition: ConditionFrame,
    else_condition: ConditionFrame,
) -> ConditionFrame:
    """logic.if-else. 두 분기가 조건이면 고른 쪽의 참·거짓을 남긴다."""
    then_map = then_condition.matches()
    else_map = else_condition.matches()
    rows: list[ConditionRow] = []
    for row in condition.rows:
        source = then_map if row.matched else else_map
        if row.instrument_id not in source:
            raise ValueError(f"{row.instrument_id}: 선택한 분기에 조건이 없습니다.")
        rows.append(ConditionRow(row.instrument_id, source[row.instrument_id]))
    return ConditionFrame(tuple(rows))


def set_condition_operation(left: ConditionFrame, right: ConditionFrame, operator: str) -> ConditionFrame:
    """logic.set-op. 참인 종목만 집합 연산하고, 나머지 종목은 거짓으로 남긴다."""
    chosen = set(set_operation(
        {row.instrument_id for row in left.rows if row.matched},
        {row.instrument_id for row in right.rows if row.matched},
        operator,
    ))
    order: list[str] = []
    seen: set[str] = set()
    for row in (*left.rows, *right.rows):
        if row.instrument_id not in seen:
            seen.add(row.instrument_id)
            order.append(row.instrument_id)
    return _condition({symbol: symbol in chosen for symbol in order})


def set_operation(left: set[str], right: set[str], operator: str) -> tuple[str, ...]:
    """logic.set-op. 합집합, 교집합, 차집합."""
    if operator == "union":
        result = left | right
    elif operator == "intersect":
        result = left & right
    elif operator == "difference":
        result = left - right
    else:
        raise ValueError(f"지원하지 않는 집합 연산입니다: {operator}")
    return tuple(sorted(result))


def allocate_target_weight(selection: SelectionFrame, scheme: str = "equal") -> WeightFrame:
    """allocation.target-weight."""
    if scheme == "equal":
        return weight_equal(selection)
    if scheme == "score":
        return weight_by_score(selection)
    raise ValueError(f"지원하지 않는 배분 방식입니다: {scheme}")


def apply_stop(
    weights: WeightFrame,
    *,
    entry_prices: Mapping[str, float],
    last_prices: Mapping[str, float],
    peak_prices: Mapping[str, float] | None = None,
    stop_loss_pct: float = 10,
    take_profit_pct: float = 30,
    trailing: bool = False,
) -> WeightFrame:
    """allocation.stop. 손절 또는 익절이면 그 종목 비중을 0으로 만든다."""
    if stop_loss_pct < 0 or take_profit_pct < 0:
        raise ValueError("손절·익절 비율은 0 이상이어야 합니다.")
    if trailing and peak_prices is None:
        raise ValueError("고점 대비 손절에는 보유 중 고가가 필요합니다.")
    rows: list[WeightRow] = []
    for row in weights.rows:
        if row.weight == 0:
            rows.append(row)
            continue
        entry = entry_prices[row.instrument_id]
        last = last_prices[row.instrument_id]
        stop_base = peak_prices[row.instrument_id] if trailing and peak_prices is not None else entry
        stopped = last <= stop_base * (1 - stop_loss_pct / 100)
        took_profit = last >= entry * (1 + take_profit_pct / 100)
        rows.append(WeightRow(row.instrument_id, 0.0 if stopped or took_profit else row.weight))
    return WeightFrame(tuple(rows))


def rebalance_weights(
    target: WeightFrame,
    actual: WeightFrame,
    *,
    due: bool,
    drift_threshold_pct: float = 5,
) -> WeightFrame:
    """allocation.rebalance. 정기 시점이거나 이탈이 크면 목표 비중을, 아니면 현재 비중을 반환한다."""
    if drift_threshold_pct < 0:
        raise ValueError("이탈 허용은 0 이상이어야 합니다.")
    if due:
        return target
    target_map = {row.instrument_id: row.weight for row in target.rows}
    actual_map = {row.instrument_id: row.weight for row in actual.rows}
    symbols = set(target_map) | set(actual_map)
    drifted = any(
        abs(actual_map.get(symbol, 0.0) - target_map.get(symbol, 0.0)) >= drift_threshold_pct / 100
        for symbol in symbols
    )
    return target if drifted else actual


def _upto(history: History, symbol: str, as_of: datetime) -> Sequence[MarketBar]:
    return bars_until(history, symbol, as_of)


def _tail_returns(series: Sequence[MarketBar], window: int) -> list[float]:
    closes = [bar.close for bar in series]
    if len(closes) < window + 1:
        return []
    tail = closes[-(window + 1):]
    return [tail[index] / tail[index - 1] - 1 for index in range(1, len(tail)) if tail[index - 1] > 0]


def _stdev(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)


def _beta_scores(
    history: History,
    symbols: Sequence[str],
    as_of: datetime,
    window_days: int,
) -> ScoreFrame:
    by_symbol = {symbol: {bar.timestamp: bar.close for bar in _upto(history, symbol, as_of)} for symbol in symbols}
    dates = sorted({day for closes in by_symbol.values() for day in closes})
    market: dict[datetime, float] = {}
    for previous, day in zip(dates, dates[1:]):
        returns = [
            closes[day] / closes[previous] - 1
            for closes in by_symbol.values()
            if previous in closes and day in closes and closes[previous] > 0
        ]
        if returns:
            market[day] = sum(returns) / len(returns)
    rows: list[ScoreRow] = []
    market_days = set(dates[-window_days:])
    for symbol in symbols:
        closes = by_symbol[symbol]
        paired = [
            (closes[day] / closes[previous] - 1, market[day])
            for previous, day in zip(dates, dates[1:])
            if day in market_days and day in market and previous in closes and day in closes and closes[previous] > 0
        ]
        if len(paired) < window_days:
            continue
        stock = [item[0] for item in paired]
        bench = [item[1] for item in paired]
        variance = sum((value - sum(bench) / len(bench)) ** 2 for value in bench)
        if variance == 0:
            continue
        mean_stock = sum(stock) / len(stock)
        mean_bench = sum(bench) / len(bench)
        covariance = sum((left - mean_stock) * (right - mean_bench) for left, right in zip(stock, bench))
        beta = covariance / variance
        rows.append(ScoreRow(symbol, -beta, raw_value=beta))
    return ScoreFrame(tuple(rows))
