"""화면 블록의 계산.

`n10_functions`의 블록 id와 대응한다. 점수가 높을수록 더 선호되는 종목이다.
재무 지표는 재무 데이터가 필요해서 이 모듈에 없다.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from ai_studio.n01_frames import (
    EventFrame,
    EventRow,
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
from ai_studio.n03_market_data import Fundamentals, History, bars_until, filing_as_of, session_date
from ai_studio.n05_signals import OPERATORS
from ai_studio.n04_indicators import daily_returns, moving_average, rsi, stdev, total_return, window_high
from ai_studio.n06_portfolio import weight_by_score, weight_equal



def compose_universe(history: History, as_of: datetime) -> UniverseFrame:
    """universe.compose. as_of까지 봉이 하나라도 공개된 종목. 아직 상장하지 않은 종목은 넣지 않는다."""
    symbols = tuple(sorted(symbol for symbol in history if _upto(history, symbol, as_of)))
    return _universe(as_of, symbols)


def fundamental_universe(
    fundamentals: Fundamentals,
    symbols: Sequence[str],
    as_of: datetime,
    *,
    account: str,
    operator: str,
    threshold: float,
) -> UniverseFrame:
    """universe.fundamental. as_of까지 공개된 가장 최근 분기 값으로 거른다. 공시가 없는 종목은 뺀다."""
    compare = OPERATORS.get(operator)
    if compare is None:
        raise ValueError(f"지원하지 않는 비교입니다: {operator}")
    kept = [
        symbol for symbol in symbols
        if (filing := filing_as_of(fundamentals, symbol, account, as_of)) is not None
        and compare(filing.value, threshold)
    ]
    return _universe(as_of, kept)


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


def allocate_target_weight(selection: SelectionFrame, scheme: str = "equal") -> WeightFrame:
    """allocation.target-weight."""
    if scheme == "equal":
        return weight_equal(selection)
    if scheme == "score":
        return weight_by_score(selection)
    raise ValueError(f"지원하지 않는 배분 방식입니다: {scheme}")


def _upto(history: History, symbol: str, as_of: datetime) -> Sequence[MarketBar]:
    return bars_until(history, symbol, as_of)


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


# ── 원자 지표 블록 ─────────────────────────────────────────────
# 모든 지표는 판단 없이 숫자만 낸다. scope="market"이면 동일가중 시장 지수 한 줄(MARKET)을 낸다.

MARKET_ID = "MARKET"
_MARKET_CACHE: list[tuple[object, tuple[MarketBar, ...]]] = []


def market_series(history: History) -> tuple[MarketBar, ...]:
    """적재된 모든 종목의 일간 수익률을 같은 비중으로 평균해 100에서 이어 붙인 지수.
    t일 값은 t-1일과 t일 종가만 쓰므로 미래 데이터를 보지 않는다. 지수 데이터가 생기면 그것으로 바꾼다."""
    for source, series in _MARKET_CACHE:
        if source is history:
            return series
    closes: dict[datetime, dict[str, float]] = {}
    for symbol, bars in history.items():
        for bar in bars:
            closes.setdefault(bar.timestamp, {})[symbol] = bar.close
    level = 100.0
    previous: dict[str, float] | None = None
    output: list[MarketBar] = []
    for stamp in sorted(closes):
        today = closes[stamp]
        if previous is not None:
            moves = [today[s] / previous[s] - 1 for s in today if s in previous]
            if moves:
                level *= 1 + sum(moves) / len(moves)
        output.append(MarketBar(MARKET_ID, stamp, level, level, level, level))
        previous = today
    series = tuple(output)
    _MARKET_CACHE[:] = [(history, series)] + _MARKET_CACHE[:3]
    return series


def _scoped(history: History, symbols: Sequence[str], scope: str) -> tuple[History, Sequence[str]]:
    if scope == "symbol":
        return history, symbols
    if scope == "market":
        return {MARKET_ID: market_series(history)}, (MARKET_ID,)
    raise ValueError(f"지원하지 않는 범위입니다: {scope}")


def _indicator(history: History, symbols: Sequence[str], as_of: datetime, scope: str,
               minimum: int, compute) -> IndicatorFrame:
    source, ids = _scoped(history, symbols, scope)
    rows: list[IndicatorRow] = []
    for symbol in ids:
        series = _upto(source, symbol, as_of)
        if len(series) >= minimum:
            rows.append(IndicatorRow(symbol, compute(series)))
    return IndicatorFrame(tuple(rows))


def return_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                     lookback_days: int = 252, skip_days: int = 0, scope: str = "symbol") -> IndicatorFrame:
    """indicator.return. 최근 skip_days를 뺀 lookback_days 수익률."""
    if lookback_days < 1 or skip_days < 0:
        raise ValueError("수익률 기간은 1일 이상, 제외 기간은 0일 이상이어야 합니다.")
    return _indicator(history, symbols, as_of, scope, lookback_days + skip_days + 1,
                      lambda s: total_return([bar.close for bar in s], lookback_days, skip_days))


def moving_average_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                             window_days: int = 20, scope: str = "symbol") -> IndicatorFrame:
    """indicator.moving-average. 종가 단순이동평균."""
    if window_days < 1:
        raise ValueError("이동평균 기간은 1일 이상이어야 합니다.")
    return _indicator(history, symbols, as_of, scope, window_days,
                      lambda s: moving_average([bar.close for bar in s], window_days))


def price_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                    field: str = "close", scope: str = "symbol") -> IndicatorFrame:
    """indicator.price. as_of 시점 마지막 봉의 가격."""
    if field not in ("open", "high", "low", "close"):
        raise ValueError(f"지원하지 않는 가격입니다: {field}")
    return _indicator(history, symbols, as_of, scope, 1, lambda s: getattr(s[-1], field))


def high_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                   window_days: int = 252, scope: str = "symbol") -> IndicatorFrame:
    """indicator.high. 오늘을 뺀 직전 window_days 고가의 최고치. 오늘 종가와 비교하면 돌파가 된다."""
    if window_days < 1:
        raise ValueError("고점 기간은 1일 이상이어야 합니다.")
    return _indicator(history, symbols, as_of, scope, window_days + 1,
                      lambda s: window_high([bar.high for bar in s[:-1]], window_days))


def volatility_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                         window_days: int = 252, metric: str = "stdev", scope: str = "symbol") -> IndicatorFrame:
    """indicator.volatility. stdev는 일간 수익률 표준편차, beta는 동일가중 시장 대비 베타."""
    if window_days < 2:
        raise ValueError("변동성 기간은 2일 이상이어야 합니다.")
    if metric == "stdev":
        return _indicator(history, symbols, as_of, scope, window_days + 1,
                          lambda s: stdev(daily_returns([bar.close for bar in s], window_days)))
    if metric == "beta":
        if scope == "market":
            return IndicatorFrame((IndicatorRow(MARKET_ID, 1.0),))
        frame = _beta_scores(history, symbols, as_of, window_days)
        return IndicatorFrame(tuple(IndicatorRow(row.instrument_id, row.raw_value) for row in frame.rows))
    raise ValueError(f"지원하지 않는 변동성 지표입니다: {metric}")


def rsi_indicator(history: History, symbols: Sequence[str], as_of: datetime, *,
                        period: int = 14, scope: str = "symbol") -> IndicatorFrame:
    """indicator.rsi. 상승폭 평균과 하락폭 평균으로 계산한 RSI."""
    return _indicator(history, symbols, as_of, scope, period + 1,
                      lambda s: rsi([bar.close for bar in s], period))


def eligible_scores(scores: ScoreFrame, condition: ConditionFrame) -> ScoreFrame:
    """selection.top-n의 대상 조건. 조건이 참인 종목의 점수만 남긴다. 시장 한 줄 조건이면 전체를 남기거나 비운다."""
    flags = condition.matches()
    if list(flags) == [MARKET_ID]:
        return scores if flags[MARKET_ID] else ScoreFrame(())
    return ScoreFrame(tuple(row for row in scores.rows if flags.get(row.instrument_id, False)))


# ── 보유 기반 청산 이벤트 ──────────────────────────────────────


def stop_exit_events(history: History, positions: Mapping[str, object], as_of: datetime, *,
                     stop_loss_pct: float = 10, take_profit_pct: float = 0, trailing: bool = False) -> EventFrame:
    """event.stop. 보유 종목의 종가가 평균진입가(또는 진입 후 고점) 대비 손절선 이하이거나
    익절선 이상이면 청산 이벤트. 비율 0은 그 규칙을 쓰지 않는다."""
    if stop_loss_pct < 0 or take_profit_pct < 0:
        raise ValueError("손절·익절 비율은 0 이상이어야 합니다.")
    rows: list[EventRow] = []
    for symbol, position in sorted(positions.items()):
        if not getattr(position, "quantity", 0):
            continue
        series = _upto(history, symbol, as_of)
        if not series:
            continue
        last = series[-1].close
        entry = position.average_entry_price
        opened = position.opened_at
        if trailing:
            held = [bar.high for bar in series if opened is None or bar.timestamp >= opened]
            base = max(held + [entry])
        else:
            base = entry
        stopped = stop_loss_pct > 0 and last <= base * (1 - stop_loss_pct / 100)
        profited = take_profit_pct > 0 and last >= entry * (1 + take_profit_pct / 100)
        if stopped or profited:
            rows.append(EventRow(symbol, as_of, "exit"))
    return EventFrame(tuple(rows))


def holding_period_exit_events(history: History, positions: Mapping[str, object], as_of: datetime, *,
                               max_days: int = 20) -> EventFrame:
    """event.holding-period. 진입 후 거래일 수가 max_days 이상이면 청산 이벤트."""
    if max_days < 1:
        raise ValueError("보유 기간은 1일 이상이어야 합니다.")
    rows: list[EventRow] = []
    for symbol, position in sorted(positions.items()):
        if not getattr(position, "quantity", 0) or position.opened_at is None:
            continue
        held = [bar for bar in _upto(history, symbol, as_of) if bar.timestamp >= position.opened_at]
        if len(held) >= max_days:
            rows.append(EventRow(symbol, as_of, "exit"))
    return EventFrame(tuple(rows))
