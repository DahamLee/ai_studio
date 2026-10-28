"""블록 id와 계산 함수의 대응.

실행기는 이 목록만 보고 함수를 찾는다. 새 구현은 register로 추가하고 실행기는 그대로 둔다.
핸들러는 (입력 포트 값, 기본값이 채워진 파라미터, 실행 컨텍스트)를 받아 {출력 포트: 값}을 돌려준다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar

from ai_studio.n01_frames import (
    ConditionFrame, EventFrame, EventPlan, IndicatorFrame, ScoreFrame, SelectionFrame, TargetPortfolio, UniverseFrame,
    WeightFrame,
)
from ai_studio.n03_market_data import Fundamentals, session_date
from ai_studio.n05_signals import compare_indicators, compare_threshold, negate, transition_events
from ai_studio.n06_portfolio import (
    apply_cash_weight, cap_max_weight, combine_scores, scale_when_false, score_from_indicator, select_matched,
    select_min_score, select_top_n, select_top_n_with_buffer, to_target_portfolio, weight_by_indicator,
)
from ai_studio.n07_runtime import evaluate_events, evaluate_state
from ai_studio.n08_blocks import (
    allocate_target_weight, combine_conditions, compose_universe, eligible_scores, exclude_universe,
    fundamental_indicator, fundamental_universe,
    high_indicator, holding_period_exit_events, if_else_condition, if_else_score, min_price_universe,
    moving_average_indicator, price_indicator, return_indicator, rsi_indicator, stop_exit_events,
    volatility_indicator,
)
from ai_studio.n10_functions import get_function

if TYPE_CHECKING:
    from ai_studio.n14_engine import ExecutionContext

Inputs = Mapping[str, object]
Params = Mapping[str, object]
Handler = Callable[[Inputs, Params, "ExecutionContext"], dict[str, object]]
T = TypeVar("T")

_HANDLERS: dict[str, Handler] = {}


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


def _expect(inputs: Inputs, key: str, kind: type[T]) -> T:
    value = inputs.get(key)
    if not isinstance(value, kind):
        raise ValueError(f"{key} 입력은 {kind.__name__}이어야 합니다.")
    return value


def _held(context: ExecutionContext) -> set[str]:
    return {symbol for symbol, position in context.positions.items() if position.quantity > 0}


# ── 유니버스 ──────────────────────────────────────────────────


def _compose(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    if params["benchmark"] != "SPX" or params["market"] != "US_EQUITY":
        raise ValueError("적재된 일봉은 S&P 500 미국 주식만 포함합니다.")
    if float(params["minMarketCap"]) > 0:  # type: ignore[arg-type]
        raise ValueError("시가총액 필터는 시가총액 데이터가 필요합니다.")
    return {"universe": compose_universe(context.history, context.as_of)}


def _exclude(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    universe = _expect(inputs, "universe", UniverseFrame)
    return {"universe": exclude_universe(
        context.history, universe.symbols, context.as_of,
        min_listing_days=int(params["minListingDays"]),  # type: ignore[arg-type]
        min_average_turnover=float(params["minAverageTurnover"]),  # type: ignore[arg-type]
        exclude_halted=bool(params["excludeHalted"]),
        exclude_financials=bool(params["excludeFinancials"]),
        exclude_loss_making=bool(params["excludeLossMaking"]),
    )}


def _min_price(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    universe = _expect(inputs, "universe", UniverseFrame)
    return {"universe": min_price_universe(context.history, universe.symbols, context.as_of,
                                           min_close=float(params["minClose"]))}  # type: ignore[arg-type]


def _fundamentals(context: ExecutionContext, name: str) -> Fundamentals:
    if context.fundamentals is None:
        raise ValueError(f"{name}에는 재무 공시 데이터가 필요합니다. 일봉만으로는 계산하지 않습니다.")
    return context.fundamentals


def _fundamental_universe(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    universe = _expect(inputs, "universe", UniverseFrame)
    return {"universe": fundamental_universe(
        _fundamentals(context, "재무 조건으로 거르기"), universe.symbols, context.as_of,
        account=str(params["account"]), operator=str(params["operator"]), threshold=float(params["threshold"]),  # type: ignore[arg-type]
    )}


# ── 지표 ──────────────────────────────────────────────────────


def _indicator(compute: Callable[..., IndicatorFrame], *, scoped: bool = True,
               **names: tuple[str, Callable[[object], object]]) -> Handler:
    """파라미터 키(camelCase)를 함수 인자로 옮기는 지표 핸들러. names는 {인자: (키, 형변환)}."""
    def handler(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
        universe = _expect(inputs, "universe", UniverseFrame)
        kwargs = {arg: cast(params[key]) for arg, (key, cast) in names.items()}
        if scoped:
            kwargs["scope"] = str(params["scope"])
        return {"value": compute(context.history, universe.symbols, context.as_of, **kwargs)}
    return handler


def _fundamental(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    universe = _expect(inputs, "universe", UniverseFrame)
    return {"value": fundamental_indicator(context.history, _fundamentals(context, "재무 지표 가져오기"),
                                           universe.symbols, context.as_of, field=str(params["field"]))}


# ── 점수·조건·논리 ────────────────────────────────────────────


def _score(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"score": score_from_indicator(_expect(inputs, "value", IndicatorFrame),
                                          str(params["direction"]), str(params["method"]))}


def _score_combine(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"score": combine_scores(_expect(inputs, "left", ScoreFrame), _expect(inputs, "right", ScoreFrame),
                                    float(params["leftWeightPct"]) / 100)}  # type: ignore[arg-type]


def _threshold(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"condition": compare_threshold(_expect(inputs, "value", IndicatorFrame), str(params["operator"]),
                                           float(params["threshold"]))}  # type: ignore[arg-type]


def _compare(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"condition": compare_indicators(
        _expect(inputs, "left", IndicatorFrame), _expect(inputs, "right", IndicatorFrame),
        str(params["operator"]), float(params["offsetPct"]),  # type: ignore[arg-type]
    )}


def _combine(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    conditions = inputs["conditions"]
    if not isinstance(conditions, list) or not all(isinstance(item, ConditionFrame) for item in conditions):
        raise ValueError("조건 조합의 입력은 조건 프레임 목록이어야 합니다.")
    return {"condition": combine_conditions(str(params["operator"]), *conditions)}


def _not(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"condition": negate(_expect(inputs, "condition", ConditionFrame))}


def _if_else(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    condition = _expect(inputs, "condition", ConditionFrame)
    then_branch, else_branch = inputs["thenBranch"], inputs["elseBranch"]
    if isinstance(then_branch, ScoreFrame) and isinstance(else_branch, ScoreFrame):
        return {"result": if_else_score(condition, then_branch, else_branch)}
    if isinstance(then_branch, ConditionFrame) and isinstance(else_branch, ConditionFrame):
        return {"result": if_else_condition(condition, then_branch, else_branch)}
    raise ValueError("IF의 두 분기는 같은 종류의 점수 또는 조건이어야 합니다.")


# ── 선정·이벤트 ───────────────────────────────────────────────


def _top_n(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    scores = _expect(inputs, "score", ScoreFrame)
    eligible = inputs.get("eligible")
    if eligible is not None:
        scores = eligible_scores(scores, _expect(inputs, "eligible", ConditionFrame))
    top_n, buffer = int(params["topN"]), int(params["bufferN"])  # type: ignore[arg-type]
    if buffer == 0:
        return {"selection": select_top_n(scores, top_n)}
    return {"selection": select_top_n_with_buffer(scores, top_n, _held(context), buffer)}


def _min_score(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"selection": select_min_score(_expect(inputs, "score", ScoreFrame),
                                          float(params["minScore"]))}  # type: ignore[arg-type]


def _select_condition(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"selection": select_matched(_expect(inputs, "condition", ConditionFrame))}


def _transition(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    current = _expect(inputs, "condition", ConditionFrame)
    previous = None if context.previous_inputs is None else context.previous_inputs.get("condition")
    if previous is not None and not isinstance(previous, ConditionFrame):
        raise ValueError("직전 조건은 조건 프레임이어야 합니다.")
    entry, exit_ = transition_events(previous, current, context.as_of)
    return {"entry": entry, "exit": exit_}


def _stop(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"exit": stop_exit_events(
        context.history, context.positions, context.as_of,
        stop_loss_pct=float(params["stopLossPct"]),  # type: ignore[arg-type]
        take_profit_pct=float(params["takeProfitPct"]),  # type: ignore[arg-type]
        trailing=bool(params["trailing"]),
    )}


def _holding_period(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"exit": holding_period_exit_events(context.history, context.positions, context.as_of,
                                               max_days=int(params["maxDays"]))}  # type: ignore[arg-type]


# ── 비중·제약·목표 ────────────────────────────────────────────


def _target_weight(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"weight": allocate_target_weight(_expect(inputs, "selection", SelectionFrame), str(params["scheme"]))}


def _indicator_weight(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"weight": weight_by_indicator(_expect(inputs, "selection", SelectionFrame),
                                          _expect(inputs, "value", IndicatorFrame), str(params["direction"]))}


def _event_slots(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    gate = inputs.get("gate")
    eligible: tuple[str, ...] | None = None
    priority: tuple[str, ...] = ()
    if isinstance(gate, ConditionFrame):
        eligible = tuple(symbol for symbol, matched in gate.matches().items() if matched)
    elif isinstance(gate, SelectionFrame):
        priority = tuple(row.instrument_id for row in sorted(gate.rows, key=lambda row: row.rank))
        eligible = priority
    elif gate is not None:
        raise ValueError("진입 게이트는 조건 또는 선정 결과여야 합니다.")
    return {"plan": EventPlan(
        tuple(sorted(_event_union(inputs["entry"], context.as_of))),
        tuple(sorted(_event_union(inputs["exit"], context.as_of))),
        int(params["maxPositions"]), eligible, priority,  # type: ignore[arg-type]
    )}


def _max_weight(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"weight": cap_max_weight(_expect(inputs, "weight", WeightFrame),
                                     float(params["maxWeightPct"]) / 100)}  # type: ignore[arg-type]


def _cash(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"weight": apply_cash_weight(_expect(inputs, "weight", WeightFrame),
                                        float(params["cashWeightPct"]) / 100)}  # type: ignore[arg-type]


def _risk_off(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"weight": scale_when_false(_expect(inputs, "weight", WeightFrame), _expect(inputs, "condition", ConditionFrame),
                                       float(params["keepWeightPct"]) / 100)}  # type: ignore[arg-type]


def _portfolio(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    return {"portfolio": to_target_portfolio(_expect(inputs, "weight", WeightFrame))}


# ── 전략 판단·실행 ────────────────────────────────────────────


def _state(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    # 유니버스는 앞 단계 데이터에 이미 반영돼 있다. 판단 대상은 목표 종목과 현재 보유 종목이다.
    portfolio = _expect(inputs, "portfolio", TargetPortfolio)
    targets = {row.instrument_id: row.target_weight for row in portfolio.positions}
    universe = UniverseFrame(context.as_of, tuple(sorted(set(targets) | _held(context))))
    return {"result": evaluate_state("state", universe, targets, positions=context.positions)}


def _event(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    plan = _expect(inputs, "plan", EventPlan)
    # 판단 대상은 신호가 난 종목, 게이트 통과 종목, 현재 보유 종목이다.
    symbols = set(plan.entries) | set(plan.exits) | set(plan.eligible or ()) | _held(context)
    universe = UniverseFrame(context.as_of, tuple(sorted(symbols)))
    eligible = None if plan.eligible is None else dict.fromkeys(plan.eligible, True)
    return {"result": evaluate_events(
        "event", universe, set(plan.entries), set(plan.exits), context.positions,
        eligible=eligible, max_positions=plan.max_positions, priority=plan.priority or None,
    )}


def _execution(inputs: Inputs, params: Params, context: ExecutionContext) -> dict[str, object]:
    """실행 노드는 평가 시점마다 아무것도 계산하지 않는다. 주문과 체결은 n15의 실행기가 맡는다."""
    return {}


def _event_union(values: object, as_of: datetime) -> set[str]:
    """평가일과 같은 거래일에 발생한 이벤트의 종목. 여러 이벤트 입력은 합친다."""
    frames = values if isinstance(values, list) else [values]
    day = session_date(as_of)
    symbols: set[str] = set()
    for frame in frames:
        if not isinstance(frame, EventFrame):
            raise ValueError("이벤트 입력은 이벤트 프레임이어야 합니다.")
        symbols.update(row.instrument_id for row in frame.rows
                       if row.as_of <= as_of and session_date(row.as_of) == day)
    return symbols


def register_builtin_handlers() -> None:
    pairs: tuple[tuple[str, Handler], ...] = (
        ("universe.compose", _compose),
        ("universe.exclude", _exclude),
        ("universe.fundamental", _fundamental_universe),
        ("universe.min-price", _min_price),
        ("indicator.return", _indicator(return_indicator, lookback_days=("lookbackDays", int), skip_days=("skipDays", int))),
        ("indicator.moving-average", _indicator(moving_average_indicator, window_days=("windowDays", int))),
        ("indicator.price", _indicator(price_indicator, field=("field", str))),
        ("indicator.high", _indicator(high_indicator, window_days=("windowDays", int))),
        ("indicator.volatility", _indicator(volatility_indicator, window_days=("windowDays", int), metric=("metric", str))),
        ("indicator.rsi", _indicator(rsi_indicator, period=("period", int))),
        ("indicator.fundamental", _fundamental),
        ("score.from-indicator", _score),
        ("score.combine", _score_combine),
        ("condition.threshold", _threshold),
        ("condition.compare", _compare),
        ("logic.combine", _combine),
        ("logic.not", _not),
        ("logic.if-else", _if_else),
        ("selection.top-n", _top_n),
        ("selection.threshold", _min_score),
        ("selection.condition", _select_condition),
        ("event.transition", _transition),
        ("event.stop", _stop),
        ("event.holding-period", _holding_period),
        ("allocation.target-weight", _target_weight),
        ("allocation.indicator-weight", _indicator_weight),
        ("allocation.event-slots", _event_slots),
        ("portfolio.max-weight", _max_weight),
        ("portfolio.cash", _cash),
        ("portfolio.risk-off", _risk_off),
        ("portfolio.target", _portfolio),
        ("strategy.state", _state),
        ("strategy.event", _event),
        ("execution.orders", _execution),
    )
    for function_id, handler in pairs:
        register(function_id, handler)


register_builtin_handlers()
