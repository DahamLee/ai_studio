"""블록 id와 계산 함수의 대응.

실행기는 이 목록만 보고 함수를 찾는다. 새 구현은 register로 추가하고 실행기는 그대로 둔다.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, time, timezone

from ai_studio.n08_blocks import (
    apply_stop,
    breakout_condition,
    combine_conditions,
    compose_universe,
    exclude_universe,
    min_price_universe,
    if_else_score,
    if_else_condition,
    low_volatility_score,
    momentum_score,
    moving_average_cross,
    rebalance_weights,
    rsi_indicator,
    set_operation,
    set_condition_operation,
    trend_condition,
)
from ai_studio.n01_frames import EventFrame, ScoreFrame, ConditionFrame, UniverseFrame, UniverseRow, WeightFrame
from ai_studio.n10_functions import get_function
from ai_studio.n06_portfolio import (
    apply_cash_weight,
    cap_max_weight,
    select_min_score,
    select_top_n,
    to_target_portfolio,
    weight_by_score,
    weight_equal,
)
from ai_studio.n05_signals import compare_threshold
from ai_studio.n07_runtime import UniverseFrame as OrderUniverse
from ai_studio.n07_runtime import evaluate_events, evaluate_state

Handler = Callable[[Mapping[str, object], Mapping[str, object], object], dict[str, object]]

_HANDLERS: dict[str, Handler] = {}
_CLOSE = time(21, 0, tzinfo=timezone.utc)


def register(function_id: str, handler: Handler) -> None:
    get_function(function_id)
    _HANDLERS[function_id] = handler


def get_handler(function_id: str) -> Handler:
    try:
        return _HANDLERS[function_id]
    except KeyError as error:
        raise KeyError(f"{function_id}: 등록된 계산 함수가 없습니다.") from error


def registered_ids() -> tuple[str, ...]:
    return tuple(sorted(_HANDLERS))


def _compose(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    if params.get("benchmark", "SPX") != "SPX" or params.get("market", "US_EQUITY") != "US_EQUITY":
        raise ValueError("적재된 일봉은 S&P 500 미국 주식만 포함합니다.")
    if float(params.get("minMarketCap") or 0) > 0:
        raise ValueError("시가총액 필터는 시가총액 데이터가 필요합니다.")
    return {"universe": compose_universe(_history(context), _as_of(context))}


def _exclude(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"universe": exclude_universe(
        _history(context), universe.symbols(), _as_of(context),
        min_listing_days=int(params["minListingDays"]),
        min_average_turnover=float(params["minAverageTurnover"]),
        exclude_halted=bool(params["excludeHalted"]),
        exclude_financials=bool(params["excludeFinancials"]),
        exclude_loss_making=bool(params["excludeLossMaking"]),
    )}


def _min_price(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"universe": min_price_universe(
        _history(context), universe.symbols(), _as_of(context),
        min_close=float(params["minClose"]),
    )}


def _momentum(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"score": momentum_score(
        _history(context), universe.symbols(), _as_of(context),
        lookback_months=int(params["lookbackMonths"]), skip_months=int(params["skipMonths"]),
    )}


def _trend(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"condition": trend_condition(
        _history(context), universe.symbols(), _as_of(context),
        method=str(params["method"]), fast_days=int(params["fastDays"]), slow_days=int(params["slowDays"]),
    )}


def _breakout(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"condition": breakout_condition(
        _history(context), universe.symbols(), _as_of(context),
        window_days=int(params["windowDays"]), buffer_pct=float(params["bufferPct"]),
    )}


def _cross(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    events = moving_average_cross(
        _history(context), universe.symbols(),
        fast_days=int(params["fastDays"]), slow_days=int(params["slowDays"]),
    )
    return {"entry": events.of_kind("entry"), "exit": events.of_kind("exit")}


def _rsi(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"value": rsi_indicator(
        _history(context), universe.symbols(), _as_of(context), period=int(params["period"]),
    )}


def _threshold(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"condition": compare_threshold(inputs["value"], str(params["operator"]), float(params["threshold"]))}  # type: ignore[arg-type]


def _low_vol(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    return {"score": low_volatility_score(
        _history(context), universe.symbols(), _as_of(context),
        metric=str(params["metric"]), window_days=int(params["windowDays"]),
    )}


def _needs_fundamentals(name: str) -> Handler:
    def handler(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
        raise ValueError(f"{name}은 재무 데이터가 필요합니다. 일봉만으로는 계산하지 않습니다.")
    return handler


def _top_n(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"selection": select_top_n(inputs["score"], int(params["topN"]))}  # type: ignore[arg-type]


def _min_score(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"selection": select_min_score(inputs["score"], float(params["minScore"]))}  # type: ignore[arg-type]


def _target_weight(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    selection = inputs["selection"]
    scheme = str(params["scheme"])
    if scheme == "equal":
        return {"weight": weight_equal(selection)}  # type: ignore[arg-type]
    if scheme == "score":
        return {"weight": weight_by_score(selection)}  # type: ignore[arg-type]
    raise ValueError(f"지원하지 않는 배분 방식입니다: {scheme}")


def _max_weight(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"weight": cap_max_weight(inputs["weight"], float(params["maxWeightPct"]) / 100)}  # type: ignore[arg-type]


def _cash(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"weight": apply_cash_weight(inputs["weight"], float(params["cashWeightPct"]) / 100)}  # type: ignore[arg-type]


def _portfolio(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"portfolio": to_target_portfolio(inputs["weight"])}  # type: ignore[arg-type]


def _stop(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    weights = inputs["weight"]
    if not isinstance(weights, WeightFrame):
        raise ValueError("손절 입력은 비중 프레임이어야 합니다.")
    symbols = [row.instrument_id for row in weights.rows]
    return {"weight": apply_stop(
        weights,
        entry_prices=_mapping(context, "entry_prices"),
        last_prices=_last_prices(context, symbols),
        peak_prices=_mapping(context, "peak_prices") or None,
        stop_loss_pct=float(params["stopLossPct"]),
        take_profit_pct=float(params["takeProfitPct"]),
        trailing=bool(params["trailing"]),
    )}


def _rebalance(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    target = inputs["weight"]
    actual = getattr(context, "actual_weights", None)
    if not isinstance(target, WeightFrame):
        raise ValueError("리밸런스 입력은 비중 프레임이어야 합니다.")
    if not isinstance(actual, WeightFrame):
        return {"weight": target}
    return {"weight": rebalance_weights(
        target, actual, due=bool(getattr(context, "rebalance_due", False)),
        drift_threshold_pct=float(params["driftThresholdPct"]),
    )}


def _combine(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    conditions = inputs["conditions"]
    if not isinstance(conditions, list):
        conditions = [conditions]
    return {"condition": combine_conditions(str(params["operator"]), *conditions)}


def _if_else(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    condition = inputs["condition"]
    then_branch = inputs["thenBranch"]
    else_branch = inputs["elseBranch"]
    if not isinstance(condition, ConditionFrame):
        raise ValueError("IF의 조건은 조건 프레임이어야 합니다.")
    if isinstance(then_branch, ScoreFrame) and isinstance(else_branch, ScoreFrame):
        return {"result": if_else_score(condition, then_branch, else_branch)}
    if isinstance(then_branch, ConditionFrame) and isinstance(else_branch, ConditionFrame):
        return {"result": if_else_condition(condition, then_branch, else_branch)}
    raise ValueError("IF의 두 분기는 같은 종류의 점수 또는 조건이어야 합니다.")


def _set_op(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    left = inputs["left"]
    right = inputs["right"]
    operator = str(params["operator"])
    if isinstance(left, UniverseFrame) and isinstance(right, UniverseFrame):
        symbols = set_operation(set(left.symbols()), set(right.symbols()), operator)
        return {"result": UniverseFrame(left.as_of, tuple(UniverseRow(symbol) for symbol in symbols))}
    if isinstance(left, ConditionFrame) and isinstance(right, ConditionFrame):
        return {"result": set_condition_operation(left, right, operator)}
    raise ValueError("집합 연산은 유니버스 또는 조건 프레임만 받습니다.")


def _state(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    universe = _universe(inputs["universe"])
    condition = inputs["condition"]
    if not isinstance(condition, ConditionFrame):
        raise ValueError("상태 전략의 조건은 조건 프레임이어야 합니다.")
    return {"result": evaluate_state(
        "state", _order_universe(universe), condition.matches(), positions=_positions(context),
    )}


def _event(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    return {"result": _events("event", inputs, context, eligible=None)}


def _hybrid(inputs: Mapping[str, object], params: Mapping[str, object], context: object) -> dict[str, object]:
    eligible = inputs["eligible"]
    if not isinstance(eligible, ConditionFrame):
        raise ValueError("혼합 전략의 투자 가능 상태는 조건 프레임이어야 합니다.")
    return {"result": _events("hybrid", inputs, context, eligible=eligible.matches())}


def _events(strategy_id: str, inputs: Mapping[str, object], context: object, eligible: Mapping[str, bool] | None):
    universe = _universe(inputs["universe"])
    entries = _event_symbols(inputs["entry"], universe.as_of)
    exits = _event_symbols(inputs["exit"], universe.as_of)
    entry_weight = 1 / len(entries) if entries else 1.0
    return evaluate_events(
        strategy_id, _order_universe(universe), entries, exits, _positions(context),
        eligible=eligible, entry_weight=entry_weight,
    )


def _event_symbols(value: object, as_of: str) -> set[str]:
    if not isinstance(value, EventFrame):
        raise ValueError("이벤트 입력은 이벤트 프레임이어야 합니다.")
    return {row.instrument_id for row in value.rows if row.as_of.date().isoformat() == as_of}


def _order_universe(frame: UniverseFrame) -> OrderUniverse:
    stamp = datetime.combine(datetime.strptime(frame.as_of, "%Y-%m-%d").date(), _CLOSE)
    return OrderUniverse(stamp, frame.symbols())


def _universe(value: object) -> UniverseFrame:
    if not isinstance(value, UniverseFrame):
        raise ValueError("유니버스 입력은 UniverseFrame이어야 합니다.")
    return value


def _history(context: object):
    return getattr(context, "history")


def _as_of(context: object) -> str:
    return str(getattr(context, "as_of"))


def _mapping(context: object, name: str) -> Mapping[str, float]:
    value = getattr(context, name, {})
    return value or {}


def _positions(context: object):
    return getattr(context, "positions", {}) or {}


def _last_prices(context: object, symbols: Sequence[str]) -> dict[str, float]:
    history = _history(context)
    as_of = _as_of(context)
    prices: dict[str, float] = {}
    for symbol in symbols:
        series = [bar for bar in history.get(symbol, ()) if bar.date <= as_of]
        if not series:
            raise ValueError(f"{symbol}: {as_of} 종가가 없습니다.")
        prices[symbol] = series[-1].close
    return prices


def register_builtin_handlers() -> None:
    pairs = (
        ("universe.compose", _compose),
        ("universe.exclude", _exclude),
        ("universe.min-price", _min_price),
        ("momentum.factor", _momentum),
        ("momentum.trend", _trend),
        ("momentum.breakout", _breakout),
        ("momentum.ma-cross", _cross),
        ("indicator.rsi", _rsi),
        ("condition.threshold", _threshold),
        ("factor.low-volatility", _low_vol),
        ("factor.value", _needs_fundamentals("가치 점수")),
        ("factor.quality", _needs_fundamentals("퀄리티 점수")),
        ("selection.top-n", _top_n),
        ("selection.threshold", _min_score),
        ("allocation.target-weight", _target_weight),
        ("allocation.stop", _stop),
        ("allocation.rebalance", _rebalance),
        ("portfolio.max-weight", _max_weight),
        ("portfolio.cash", _cash),
        ("portfolio.target", _portfolio),
        ("logic.combine", _combine),
        ("logic.if-else", _if_else),
        ("logic.set-op", _set_op),
        ("strategy.state", _state),
        ("strategy.event", _event),
        ("strategy.hybrid", _hybrid),
    )
    for function_id, handler in pairs:
        register(function_id, handler)


register_builtin_handlers()
