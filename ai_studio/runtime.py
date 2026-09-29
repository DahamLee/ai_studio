"""Deterministic reference contracts for no-code strategy blocks.

The registry describes the editor. Data access and production brokerage execution
are deliberately outside these pure calculation functions.
"""

from dataclasses import dataclass
from datetime import datetime
from math import floor, isfinite
from typing import Literal, Mapping, Sequence

from ai_studio.schema import ExecutionType


class StrategyValidationError(ValueError):
    pass


def _time(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyValidationError(f"{field}: 타임존이 있는 시각이 필요합니다.")


def _nonnegative(value: float, field: str) -> None:
    if not isfinite(value) or value < 0:
        raise StrategyValidationError(f"{field}: 0 이상의 유한한 값이 필요합니다.")


@dataclass(frozen=True, slots=True)
class UniverseFrame:
    as_of: datetime
    symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        _time(self.as_of, "universe.as_of")
        if len(set(self.symbols)) != len(self.symbols) or any(not s for s in self.symbols):
            raise StrategyValidationError("universe: 종목이 중복되거나 비어 있습니다.")


@dataclass(frozen=True, slots=True)
class MarketBar:
    timestamp: datetime
    available_at: datetime
    symbol: str
    open: float
    close: float
    frequency: str = "1d"
    open_at: datetime | None = None

    def __post_init__(self) -> None:
        _time(self.timestamp, "bar.timestamp")
        _time(self.available_at, "bar.available_at")
        if self.open_at is not None:
            _time(self.open_at, "bar.open_at")
            if self.open_at > self.timestamp:
                raise StrategyValidationError(f"{self.symbol}: 봉의 시가가 종가 이후일 수 없습니다.")
        if not self.symbol or not all(isfinite(p) and p > 0 for p in (self.open, self.close)):
            raise StrategyValidationError(f"bar {self.symbol} {self.timestamp}: 가격이 유효해야 합니다.")


@dataclass(frozen=True, slots=True)
class CrossEvent:
    symbol: str
    as_of: datetime
    entry: bool
    exit: bool


@dataclass(frozen=True, slots=True)
class AssetSignal:
    symbol: str
    eligible: bool | None = None
    score: float | None = None
    action: Literal["ENTER", "EXIT", "HOLD", "REBALANCE", "NONE"] = "NONE"
    desired_state: Literal["LONG", "FLAT", "SHORT", "UNCHANGED"] = "UNCHANGED"
    target_weight: float | None = None
    reason_codes: tuple[str, ...] = ()
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class StrategyResult:
    as_of: datetime
    strategy_id: str
    strategy_execution_type: ExecutionType
    universe: UniverseFrame
    signals: tuple[AssetSignal, ...]
    target_weights: tuple[tuple[str, float], ...] = ()
    reason_codes: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _time(self.as_of, "result.as_of")
        if self.as_of != self.universe.as_of:
            raise StrategyValidationError("result.as_of와 universe.as_of가 다릅니다.")
        if len(set(row.symbol for row in self.signals)) != len(self.signals):
            raise StrategyValidationError("result.signals: 중복 종목입니다.")
        if self.strategy_execution_type not in ("STATE_REBALANCE", "EVENT_LIFECYCLE", "HYBRID"):
            raise StrategyValidationError(f"{self.strategy_id}: 지원하지 않는 전략 실행유형입니다.")
        for row in self.signals:
            if row.symbol not in self.universe.symbols and not (
                self.strategy_execution_type == "STATE_REBALANCE"
                and row.desired_state == "FLAT" and row.target_weight == 0
            ):
                raise StrategyValidationError(f"{row.symbol}: 유니버스 밖 종목은 청산만 가능합니다.")
            if row.target_weight is not None:
                _nonnegative(row.target_weight, f"{row.symbol}.target_weight")
                if row.target_weight > 1:
                    raise StrategyValidationError(f"{row.symbol}: 목표 비중이 1을 초과합니다.")
            if row.score is not None and not isfinite(row.score):
                raise StrategyValidationError(f"{row.symbol}: 점수가 유한하지 않습니다.")
        if sum(row.target_weight or 0 for row in self.signals if row.target_weight) > 1 + 1e-9:
            raise StrategyValidationError("종목별 목표 비중 합계가 1을 초과합니다.")
        if len({symbol for symbol, _ in self.target_weights}) != len(self.target_weights):
            raise StrategyValidationError("목표 비중에 중복 종목이 있습니다.")
        if self.target_weights != tuple((row.symbol, row.target_weight or 0.0) for row in self.signals if row.target_weight is not None):
            if self.target_weights:
                raise StrategyValidationError("target_weights와 종목별 신호의 목표 비중이 다릅니다.")
        if sum(weight for _, weight in self.target_weights) > 1 + 1e-9:
            raise StrategyValidationError("목표 비중 합계가 1을 초과합니다.")


@dataclass(frozen=True, slots=True)
class PositionState:
    symbol: str
    quantity: float = 0
    average_entry_price: float = 0
    realized_pnl: float = 0
    opened_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _nonnegative(self.quantity, f"{self.symbol}.quantity")
        _nonnegative(self.average_entry_price, f"{self.symbol}.average_entry_price")
        if self.quantity > 0 and self.average_entry_price == 0:
            raise StrategyValidationError(f"{self.symbol}: 보유수량에 평균진입가격이 필요합니다.")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    signal_at: datetime
    event_id: str | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    signal_at: datetime
    filled_at: datetime
    price: float
    fee: float
    slippage: float
    realized_pnl: float
    event_id: str | None = None


def moving_average_cross_events(
    bars: Sequence[MarketBar], fast_days: int, slow_days: int
) -> tuple[CrossEvent, ...]:
    """Emit crossings only when both previous and current averages exist."""
    if fast_days < 1 or slow_days <= fast_days:
        raise StrategyValidationError("moving_average_cross_events: 0 < fast_days < slow_days가 필요합니다.")
    groups: dict[str, list[MarketBar]] = {}
    seen: set[tuple[str, datetime]] = set()
    for bar in bars:
        if bar.available_at < bar.timestamp:
            raise StrategyValidationError(f"{bar.symbol} {bar.timestamp}: 공개시점이 데이터 시점보다 빠릅니다.")
        key = (bar.symbol, bar.timestamp)
        if key in seen:
            raise StrategyValidationError(f"중복 시계열: {bar.symbol} {bar.timestamp}")
        seen.add(key)
        groups.setdefault(bar.symbol, []).append(bar)
    output: list[CrossEvent] = []
    for symbol, series in groups.items():
        series.sort(key=lambda bar: bar.timestamp)
        if len({bar.frequency for bar in series}) != 1:
            raise StrategyValidationError(f"{symbol}: 서로 다른 가격 주기가 섞여 있습니다.")
        close = [bar.close for bar in series]
        for i in range(slow_days, len(series)):
            if any(bar.available_at > series[i].available_at for bar in series[i-slow_days:i+1]):
                raise StrategyValidationError(f"{symbol} {series[i].timestamp}: 미래 공개 데이터가 포함됐습니다.")
            previous_fast = sum(close[i-fast_days:i]) / fast_days
            previous_slow = sum(close[i-slow_days:i]) / slow_days
            current_fast = sum(close[i-fast_days+1:i+1]) / fast_days
            current_slow = sum(close[i-slow_days+1:i+1]) / slow_days
            entry = previous_fast <= previous_slow and current_fast > current_slow
            exit_ = previous_fast >= previous_slow and current_fast < current_slow
            if entry or exit_:
                output.append(CrossEvent(symbol, series[i].available_at, entry, exit_))
    return tuple(sorted(output, key=lambda row: (row.as_of, row.symbol)))


def evaluate_state(
    strategy_id: str, universe: UniverseFrame, conditions: Mapping[str, bool],
    *, scores: Mapping[str, float] | None = None,
    positions: Mapping[str, PositionState] | None = None,
) -> StrategyResult:
    """False means target weight zero, including held names removed from the universe."""
    if set(conditions) != set(universe.symbols) or any(type(v) is not bool for v in conditions.values()):
        raise StrategyValidationError(f"{strategy_id}: 상태 조건은 유니버스의 각 종목에 bool 값이 필요합니다.")
    selected = [s for s in universe.symbols if conditions[s]]
    weight = 1 / len(selected) if selected else 0.0
    signals = tuple(
        AssetSignal(s, eligible=conditions[s], score=None if scores is None else scores.get(s),
                    action="REBALANCE", desired_state="LONG" if conditions[s] else "FLAT",
                    target_weight=weight if conditions[s] else 0.0)
        for s in universe.symbols
    ) + tuple(
        AssetSignal(s, eligible=False, action="REBALANCE", desired_state="FLAT",
                    target_weight=0.0, reason_codes=("REMOVED_FROM_UNIVERSE",))
        for s, p in sorted((positions or {}).items()) if s not in universe.symbols and p.quantity > 0
    )
    return StrategyResult(universe.as_of, strategy_id, "STATE_REBALANCE", universe, signals,
                          tuple((row.symbol, row.target_weight or 0.0) for row in signals))


def evaluate_events(
    strategy_id: str, universe: UniverseFrame, entries: set[str], exits: set[str],
    positions: Mapping[str, PositionState], *, eligible: Mapping[str, bool] | None = None,
    entry_weight: float = 1.0,
) -> StrategyResult:
    """Exit wins a conflict; false events and repeated entries preserve quantity."""
    if not entries.union(exits) <= set(universe.symbols):
        raise StrategyValidationError(f"{strategy_id}: 이벤트 종목이 유니버스 밖에 있습니다.")
    if eligible is not None and set(eligible) != set(universe.symbols):
        raise StrategyValidationError(f"{strategy_id}: 투자 가능 상태가 유니버스와 일치하지 않습니다.")
    _nonnegative(entry_weight, "entry_weight")
    if entry_weight > 1:
        raise StrategyValidationError("entry_weight가 1을 초과합니다.")
    signals: list[AssetSignal] = []
    for symbol in universe.symbols:
        long = positions.get(symbol, PositionState(symbol)).quantity > 0
        can_enter = eligible is None or eligible[symbol]
        if symbol in exits and long:
            action, desired, weight, event = "EXIT", "FLAT", 0.0, "EXIT"
        elif symbol in entries and not long and can_enter and symbol not in exits:
            action, desired, weight, event = "ENTER", "LONG", entry_weight, "ENTER"
        else:
            action, desired, weight, event = "HOLD", "UNCHANGED", None, None
        event_id = f"{strategy_id}:{symbol}:{universe.as_of.isoformat()}:{event}" if event else None
        signals.append(AssetSignal(symbol, eligible=can_enter, action=action, desired_state=desired,
                                   target_weight=weight, reason_codes=(event,) if event else (), event_id=event_id))
    mode: ExecutionType = "HYBRID" if eligible is not None else "EVENT_LIFECYCLE"
    return StrategyResult(universe.as_of, strategy_id, mode, universe, tuple(signals))


def generate_orders(
    result: StrategyResult, positions: Mapping[str, PositionState], prices: Mapping[str, float],
    *, portfolio_value: float, cash: float, rebalance_due: bool = True,
    fee_rate: float = 0.0, slippage_rate: float = 0.0,
    allow_fractional_shares: bool = False, pending: Sequence[OrderIntent] = (),
    processed_event_ids: frozenset[str] = frozenset(),
) -> tuple[OrderIntent, ...]:
    """Convert targets to order differences. Sell first and budget whole-share buys."""
    for value, name in ((portfolio_value, "portfolio_value"), (cash, "cash"),
                        (fee_rate, "fee_rate"), (slippage_rate, "slippage_rate")):
        _nonnegative(value, name)
    if result.strategy_execution_type == "STATE_REBALANCE" and not rebalance_due:
        return ()
    pending_delta: dict[str, float] = {}
    for order in pending:
        pending_delta[order.symbol] = pending_delta.get(order.symbol, 0) + order.quantity * (1 if order.side == "BUY" else -1)
    sells: list[OrderIntent] = []
    buys: list[OrderIntent] = []
    for row in result.signals:
        if row.action in ("HOLD", "NONE") or (row.event_id is not None and row.event_id in processed_event_ids):
            continue
        price = prices.get(row.symbol)
        if price is None or not isfinite(price) or price <= 0:
            raise StrategyValidationError(f"{row.symbol} {result.as_of}: 주문 기준가격이 없습니다.")
        current = positions.get(row.symbol, PositionState(row.symbol)).quantity
        if row.action == "EXIT" and pending_delta.get(row.symbol, 0) > 0:
            raise StrategyValidationError(f"{row.symbol}: 청산 전에 미체결 매수 주문을 취소해야 합니다.")
        effective = current + pending_delta.get(row.symbol, 0)
        if row.action == "EXIT":
            target = 0.0
        elif row.target_weight is not None:
            raw = portfolio_value * row.target_weight / price
            target = raw if allow_fractional_shares else floor(raw)
        else:
            continue
        delta = target - effective
        if delta < 0:
            quantity = min(-delta, max(current + min(pending_delta.get(row.symbol, 0), 0), 0))
            if quantity > 0:
                sells.append(OrderIntent(row.symbol, "SELL", quantity, result.as_of, row.event_id))
        elif delta > 0:
            buys.append(OrderIntent(row.symbol, "BUY", delta, result.as_of, row.event_id))
    budget = cash + sum(o.quantity * prices[o.symbol] * (1 - fee_rate - slippage_rate) for o in sells)
    accepted: list[OrderIntent] = []
    for order in sorted(buys, key=lambda item: item.symbol):
        unit_cost = prices[order.symbol] * (1 + fee_rate + slippage_rate)
        affordable = budget / unit_cost
        quantity = min(order.quantity, affordable if allow_fractional_shares else floor(affordable + 1e-9))
        if quantity > 0:
            accepted.append(OrderIntent(order.symbol, "BUY", quantity, order.signal_at, order.event_id))
            budget -= quantity * unit_cost
    return tuple(sells + accepted)


def fill_at_next_open(
    orders: Sequence[OrderIntent], next_bars: Mapping[str, MarketBar],
    positions: Mapping[str, PositionState], cash: float, *,
    fee_rate: float = 0.0, slippage_rate: float = 0.0,
) -> tuple[tuple[Fill, ...], dict[str, PositionState], float]:
    """Fill at a strictly later bar open; retain entry price and realized P&L."""
    _nonnegative(cash, "cash")
    holdings = dict(positions)
    fills: list[Fill] = []
    for order in orders:
        bar = next_bars.get(order.symbol)
        if bar is None or bar.open_at is None or bar.open_at <= order.signal_at:
            raise StrategyValidationError(f"{order.symbol}: 신호 이후의 시가 시각이 필요합니다.")
        price = bar.open * (1 + slippage_rate if order.side == "BUY" else 1 - slippage_rate)
        fee = order.quantity * price * fee_rate
        previous = holdings.get(order.symbol, PositionState(order.symbol))
        if order.side == "BUY":
            cost = order.quantity * price + fee
            if cost > cash + 1e-9:
                raise StrategyValidationError(f"{order.symbol}: 체결 시 가용현금 부족")
            quantity = previous.quantity + order.quantity
            average = (previous.quantity * previous.average_entry_price + order.quantity * price) / quantity
            cash -= cost
            pnl = 0.0
            opened = previous.opened_at or bar.open_at
        else:
            if order.quantity > previous.quantity:
                raise StrategyValidationError(f"{order.symbol}: 보유수량 초과 매도")
            quantity = previous.quantity - order.quantity
            average = previous.average_entry_price if quantity else 0.0
            pnl = order.quantity * (price - previous.average_entry_price) - fee
            cash += order.quantity * price - fee
            opened = previous.opened_at if quantity else None
        holdings[order.symbol] = PositionState(order.symbol, quantity, average,
                                               previous.realized_pnl + pnl, opened, bar.open_at)
        fills.append(Fill(order.symbol, order.side, order.quantity, order.signal_at,
                          bar.open_at, price, fee, abs(bar.open - price) * order.quantity, pnl,
                          order.event_id))
    return tuple(fills), holdings, cash
