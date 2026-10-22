"""등록된 블록 인스턴스 그래프를 실행기에 넘긴다.

실행: PYTHONPATH=. .venv/bin/python scripts/run_strategy.py
"""

from ai_studio.n08_blocks import history_from_bars
from ai_studio.n14_engine import ExecutionContext, execute
from ai_studio.n03_market_data import load_ohlc
from ai_studio.n12_strategies import momentum_top10


def main() -> None:
    history = history_from_bars(load_ohlc())
    as_of = max(bar.date for rows in history.values() for bar in rows)
    result = execute(momentum_top10(), ExecutionContext(as_of, history))
    target = result.port("target", "portfolio")
    print(f"as_of {as_of}")
    for position in target.positions:
        print(f"{position.instrument_id} {position.target_weight:.2%}")
    print(f"cash {target.cash_weight:.2%}")


if __name__ == "__main__":
    main()
