"""전략 판단을 주문·체결·평가로 옮기는 참조 원장.

Ledger.step은 한 평가 시점을 처리한다. 판단이 현재 보유에 따라 달라지는 이벤트 전략도
날짜마다 그래프를 실행하고 step을 부를 수 있다. 데이터 수집, 기업행동, 대차, 세금,
실제 브로커 주문 관리는 다루지 않는다. 입력을 바꾸지 않는다.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from ai_studio.n01_frames import MarketBar, StrategyValidationError
from ai_studio.n07_runtime import (
    AssetSignal, Fill, OrderIntent, PositionState, StrategyResult, execution_open, fill_at_next_open,
    generate_orders, trade_notional,
)


@dataclass(frozen=True, slots=True)
class SignalRecord:
    as_of: datetime
    strategy_id: str
    signal: AssetSignal


@dataclass(frozen=True, slots=True)
class PositionRecord:
    as_of: datetime
    symbol: str
    quantity: float
    average_entry_price: float
    market_price: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float


@dataclass(frozen=True, slots=True)
class PortfolioRecord:
    as_of: datetime
    cash: float
    securities_value: float
    total_value: float
    period_return: float
    cumulative_return: float
    drawdown: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    signal_history: tuple[SignalRecord, ...]
    trade_log: tuple[Fill, ...]
    position_history: tuple[PositionRecord, ...]
    portfolio_history: tuple[PortfolioRecord, ...]
    # 체결하지 못한 주문(다음 거래 봉 없음)과 현금 부족으로 줄어든 매수의 나머지 수량.
    skipped_orders: tuple[OrderIntent, ...] = ()


class Ledger:
    """현금과 보유를 들고 평가 시점을 하나씩 처리한다."""

    def __init__(
        self, initial_cash: float, *, fee_rate: float = 0.0, slippage_rate: float = 0.0,
        allow_fractional_shares: bool = False, shortfall: str = "error",
    ) -> None:
        if initial_cash < 0:
            raise StrategyValidationError("initial_cash는 음수일 수 없습니다.")
        self.initial_cash = initial_cash
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.allow_fractional_shares = allow_fractional_shares
        self.shortfall = shortfall
        self.cash = initial_cash
        self.holdings: dict[str, PositionState] = {}
        self._peak = initial_cash
        self._previous_value = initial_cash
        self._last_as_of: datetime | None = None
        self._processed: set[str] = set()
        self._signals: list[SignalRecord] = []
        self._trades: list[Fill] = []
        self._positions: list[PositionRecord] = []
        self._portfolio: list[PortfolioRecord] = []
        self._skipped: list[OrderIntent] = []

    def step(
        self, result: StrategyResult, prices: Mapping[str, float], next_bars: Mapping[str, MarketBar],
        *, rebalance_due: bool = True, drift_threshold: float | None = None, mark: bool = True,
    ) -> tuple[Fill, ...]:
        """t 종가 판단 → t 기준가격으로 수량 → 이후 시가 체결 → (mark면) 다음 봉 종가로 평가.

        prices는 보유 종목과 주문 종목의 t 시점 가격을 모두 담아야 한다.
        drift_threshold가 있으면 주기가 아니어도 실제 비중이 목표에서 그만큼 벗어났을 때 맞춘다.
        거래일마다 따로 mark를 부르는 실행기는 mark=False로 넘긴다.
        """
        if self._last_as_of is not None and result.as_of <= self._last_as_of:
            raise StrategyValidationError("평가 시점은 앞으로만 진행해야 합니다.")
        self._last_as_of = result.as_of
        self._signals.extend(SignalRecord(result.as_of, result.strategy_id, row) for row in result.signals)
        value = self.cash + sum(position.quantity * _price(prices, symbol, result.as_of)
                                for symbol, position in self.holdings.items() if position.quantity)
        due = rebalance_due or (drift_threshold is not None and self._drifted(result, prices, value, drift_threshold))
        orders = generate_orders(
            result, self.holdings, prices, portfolio_value=value, cash=self.cash,
            rebalance_due=due, fee_rate=self.fee_rate, slippage_rate=self.slippage_rate,
            allow_fractional_shares=self.allow_fractional_shares,
            processed_event_ids=frozenset(self._processed),
        )
        fillable = tuple(order for order in orders if order.symbol in next_bars)
        self._skipped.extend(order for order in orders if order.symbol not in next_bars)
        fills, self.holdings, self.cash = fill_at_next_open(
            fillable, next_bars, self.holdings, self.cash,
            fee_rate=self.fee_rate, slippage_rate=self.slippage_rate,
            shortfall=self.shortfall, allow_fractional_shares=self.allow_fractional_shares,  # type: ignore[arg-type]
            unfilled=self._skipped,
        )
        self._trades.extend(fills)
        self._processed.update(fill.event_id for fill in fills if fill.event_id)
        if mark:
            self._mark(result.as_of, prices, next_bars)
        return fills

    def _drifted(self, result: StrategyResult, prices: Mapping[str, float], value: float, threshold: float) -> bool:
        if result.strategy_execution_type != "STATE_REBALANCE" or value <= 0:
            return False
        targets = dict(result.target_weights)
        symbols = set(targets) | {s for s, p in self.holdings.items() if p.quantity}
        for symbol in symbols:
            quantity = self.holdings.get(symbol, PositionState(symbol)).quantity
            actual = quantity * _price(prices, symbol, result.as_of) / value if quantity else 0.0
            if abs(actual - targets.get(symbol, 0.0)) >= threshold:
                return True
        return False

    def mark(self, as_of: datetime, prices: Mapping[str, float]) -> None:
        """주문 없이 as_of 가격으로 보유를 평가해 이력에 남긴다."""
        self._record(as_of, lambda symbol: _price(prices, symbol, as_of))

    def _mark(self, signal_at: datetime, prices: Mapping[str, float], next_bars: Mapping[str, MarketBar]) -> None:
        # 다음 봉 종가는 기간 말 평가에만 쓴다. 판단이나 체결에는 쓰지 않는다.
        # 다음 봉이 없는 보유 종목은 마지막으로 알려진 가격으로 평가한다.
        as_of = max((bar.timestamp for bar in next_bars.values()), default=signal_at)

        def price_of(symbol: str) -> float:
            bar = next_bars.get(symbol)
            return bar.close if bar is not None else _price(prices, symbol, signal_at)

        self._record(as_of, price_of)

    def _record(self, as_of: datetime, price_of) -> None:
        if self._portfolio and as_of <= self._portfolio[-1].as_of:
            raise StrategyValidationError(f"{as_of}: 평가 이력은 시각순이어야 합니다.")
        securities = 0.0
        for symbol, position in sorted(self.holdings.items()):
            if not position.quantity:
                continue
            price = price_of(symbol)
            amount = position.quantity * price
            securities += amount
            self._positions.append(PositionRecord(
                as_of, symbol, position.quantity, position.average_entry_price, price,
                amount, position.realized_pnl,
                position.quantity * (price - position.average_entry_price),
            ))
        total = self.cash + securities
        self._peak = max(self._peak, total)
        self._portfolio.append(PortfolioRecord(
            as_of, self.cash, securities, total,
            total / self._previous_value - 1 if self._previous_value else 0.0,
            total / self.initial_cash - 1 if self.initial_cash else 0.0,
            total / self._peak - 1 if self._peak else 0.0,
        ))
        self._previous_value = total

    def result(self) -> BacktestResult:
        return BacktestResult(tuple(self._signals), tuple(self._trades), tuple(self._positions),
                              tuple(self._portfolio), tuple(self._skipped))


def _price(prices: Mapping[str, float], symbol: str, as_of: datetime) -> float:
    price = prices.get(symbol)
    if price is None:
        raise StrategyValidationError(f"{symbol} {as_of}: 평가 기준가격이 없습니다.")
    return price


def run_backtest(
    results: Sequence[StrategyResult],
    reference_prices: Mapping[datetime, Mapping[str, float]],
    next_bars: Mapping[datetime, Mapping[str, MarketBar]],
    *, initial_cash: float, fee_rate: float = 0.0, slippage_rate: float = 0.0,
    rebalance_dates: frozenset[datetime] | None = None,
) -> BacktestResult:
    """미리 계산한 판단을 시각순으로 원장에 넣는다. 판단이 보유에 따라 달라지면 Ledger.step을 직접 쓴다."""
    if len({result.as_of for result in results}) != len(results):
        raise StrategyValidationError("같은 평가 시점의 전략 결과가 중복됩니다.")
    ledger = Ledger(initial_cash, fee_rate=fee_rate, slippage_rate=slippage_rate)
    for result in sorted(results, key=lambda item: item.as_of):
        prices = reference_prices.get(result.as_of)
        if prices is None:
            raise StrategyValidationError(f"{result.as_of}: 평가 기준가격이 없습니다.")
        ledger.step(result, prices, next_bars.get(result.as_of, {}),
                    rebalance_due=rebalance_dates is None or result.as_of in rebalance_dates)
    return ledger.result()


TRADE_COLUMNS = (
    "signal_date", "filled_date", "symbol", "side", "quantity",
    "open", "high", "low", "close", "fill_price",
    "buy_amount", "sell_amount", "fee", "slippage", "cash_after",
)


def trade_rows(
    result: BacktestResult, *, initial_cash: float,
    ohlc: Mapping[tuple[str, str], object] | None = None,
) -> tuple[dict[str, object], ...]:
    """체결 한 건을 한 행으로 만든다. 매수금·매도금은 수수료를 넣기 전이다.

    현금은 매수일에 매수금과 수수료를 빼고, 매도일에 매도금에서 수수료를 뺀 금액을 더한다.
    `ohlc`의 키는 (종목, 체결일)이고 값은 open, high, low, close를 가진다.
    """
    if initial_cash < 0:
        raise StrategyValidationError("initial_cash는 음수일 수 없습니다.")
    cash = initial_cash
    rows: list[dict[str, object]] = []
    for fill in result.trade_log:
        buy_amount, sell_amount = trade_notional(fill)
        cash += sell_amount - buy_amount - fill.fee
        filled_date = fill.filled_at.date().isoformat()
        bar = None if ohlc is None else ohlc.get((fill.symbol, filled_date))
        rows.append({
            "signal_date": fill.signal_at.date().isoformat(),
            "filled_date": filled_date,
            "symbol": fill.symbol,
            "side": fill.side,
            "quantity": fill.quantity,
            "open": getattr(bar, "open", execution_open(fill)),
            "high": None if bar is None else bar.high,
            "low": None if bar is None else bar.low,
            "close": None if bar is None else bar.close,
            "fill_price": fill.price,
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "fee": fill.fee,
            "slippage": fill.slippage,
            "cash_after": cash,
        })
    return tuple(rows)


def write_trades_csv(
    result: BacktestResult, path: str | Path, *, initial_cash: float,
    ohlc: Mapping[tuple[str, str], object] | None = None,
) -> Path:
    """매매 내역을 CSV로 쓴다. 열은 `trade_rows`와 같다."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_COLUMNS)
        writer.writeheader()
        writer.writerows(trade_rows(result, initial_cash=initial_cash, ohlc=ohlc))
    return destination
