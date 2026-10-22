"""20거래일 수익률 상위 10종목을 21거래일마다 동일 비중으로 리밸런싱한다.

체결은 다음 시가라서 현금 20%를 남겨 둔다.

실행: python scripts/run_momentum_backtest.py
"""

from ai_studio.n15_ohlc_backtest import run_momentum_topn


def main() -> None:
    result = run_momentum_topn()
    last = result.portfolio_history[-1]
    worst = min(row.drawdown for row in result.portfolio_history)
    print(f"rebalances={len({row.as_of for row in result.signal_history})}")
    print(f"trades={len(result.trade_log)}")
    print(f"final_value={last.total_value:.2f} return={last.cumulative_return:.4%} max_drawdown={worst:.4%}")
    held = [row for row in result.position_history if row.as_of == last.as_of and row.quantity]
    print("last_holdings=" + ", ".join(f"{row.symbol}:{row.quantity:.0f}" for row in held))


if __name__ == "__main__":
    main()
