"""Reference backtest ledger for precomputed StrategyResult values.

This deliberately leaves data sourcing, corporate actions, borrow, tax and live
broker order management to production adapters. It never mutates its inputs.
"""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from ai_studio.n07_runtime import (
    AssetSignal, Fill, MarketBar, PositionState, StrategyResult,
    StrategyValidationError, execution_open, fill_at_next_open, generate_orders,
    trade_notional,
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


def run_backtest(
    results: Sequence[StrategyResult],
    reference_prices: Mapping[datetime, Mapping[str, float]],
    next_bars: Mapping[datetime, Mapping[str, MarketBar]],
    *, initial_cash: float, fee_rate: float = 0.0, slippage_rate: float = 0.0,
    rebalance_dates: frozenset[datetime] | None = None,
) -> BacktestResult:
    """Signal at t close, order from t reference, fill at supplied later open.

    Callers provide point-in-time prices and the next tradable bar explicitly.
    This is a reference ledger, not a complete exchange simulator.
    """
    if initial_cash < 0:
        raise StrategyValidationError("initial_cash는 음수일 수 없습니다.")
    if len({result.as_of for result in results}) != len(results):
        raise StrategyValidationError("같은 평가 시점의 전략 결과가 중복됩니다.")
    holdings: dict[str, PositionState] = {}
    cash = initial_cash
    peak = initial_cash
    previous_value = initial_cash
    signal_history: list[SignalRecord] = []
    trade_log: list[Fill] = []
    position_history: list[PositionRecord] = []
    portfolio_history: list[PortfolioRecord] = []
    processed: set[str] = set()
    for result in sorted(results, key=lambda item: item.as_of):
        prices = reference_prices.get(result.as_of)
        if prices is None:
            raise StrategyValidationError(f"{result.as_of}: 평가 기준가격이 없습니다.")
        signal_history.extend(SignalRecord(result.as_of, result.strategy_id, row) for row in result.signals)
        portfolio_value = cash + sum(position.quantity * prices[symbol]
                                     for symbol, position in holdings.items() if position.quantity)
        orders = generate_orders(
            result, holdings, prices, portfolio_value=portfolio_value, cash=cash,
            rebalance_due=rebalance_dates is None or result.as_of in rebalance_dates,
            fee_rate=fee_rate, slippage_rate=slippage_rate,
            processed_event_ids=frozenset(processed),
        )
        bars = next_bars.get(result.as_of, {})
        fills, holdings, cash = fill_at_next_open(
            orders, bars, holdings, cash, fee_rate=fee_rate, slippage_rate=slippage_rate,
        )
        trade_log.extend(fills)
        processed.update(fill.event_id for fill in fills if fill.event_id)
        # The next bar's close is used only for end-of-period reporting, never
        # for the earlier signal or its next-open fill.
        mark_prices = {symbol: bar.close for symbol, bar in bars.items()}
        as_of = max((bar.timestamp for bar in bars.values()), default=result.as_of)
        securities = 0.0
        for symbol, position in sorted(holdings.items()):
            if not position.quantity:
                continue
            price = mark_prices.get(symbol)
            if price is None:
                raise StrategyValidationError(f"{symbol} {as_of}: 보유 종목의 평가가격이 없습니다.")
            value = position.quantity * price
            securities += value
            position_history.append(PositionRecord(
                as_of, symbol, position.quantity, position.average_entry_price, price,
                value, position.realized_pnl,
                position.quantity * (price - position.average_entry_price),
            ))
        total = cash + securities
        peak = max(peak, total)
        portfolio_history.append(PortfolioRecord(
            as_of, cash, securities, total,
            total / previous_value - 1 if previous_value else 0.0,
            total / initial_cash - 1 if initial_cash else 0.0,
            total / peak - 1 if peak else 0.0,
        ))
        previous_value = total
    return BacktestResult(tuple(signal_history), tuple(trade_log),
                          tuple(position_history), tuple(portfolio_history))


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
