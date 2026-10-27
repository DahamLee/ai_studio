"""마지막 거래일에 각 프리셋과 모멘텀 그래프의 출력을 찍는다.

실행: PYTHONPATH=. .venv/bin/python scripts/preview_blocks.py
"""

from ai_studio.n02_schema import StrategyGraph, StrategyNode
from ai_studio.n03_market_data import history_from_bars, load_ohlc
from ai_studio.n12_strategies import PRESETS, momentum_top10, place
from ai_studio.n14_engine import ExecutionContext, execute


def main() -> None:
    history = history_from_bars(load_ohlc())
    as_of = max(bar.timestamp for rows in history.values() for bar in rows)
    print(f"기준일 {as_of}")

    result = execute(momentum_top10(), ExecutionContext(as_of, history))
    universe = result.port("universe", "universe")
    kept = result.port("filter", "universe")
    print(f"universe.compose {len(universe.symbols)} → universe.exclude 상장 148일 이상 {len(kept.symbols)}")
    print("모멘텀 6개월(최근 1개월 제외) 상위 10, 상한 15%, 현금 5%")
    for row in result.port("pick", "selection").rows:
        print(f"  {row.rank:2} {row.instrument_id:6} {row.score:+.2%}")
    for warning in result.warnings:
        print(f"  경고: {warning}")

    for preset in PRESETS:
        placed = place(preset.id, "p", {"universe": ("u", "universe")})
        graph = StrategyGraph((StrategyNode("u", "universe.compose", {}),) + placed.nodes, placed.edges)
        try:
            out = execute(graph, ExecutionContext(as_of, history))
        except ValueError as error:
            print(f"[{preset.name}] 실행 안 함: {error}")
            continue
        for key, (node, port) in placed.outputs.items():
            frame = out.port(node, port)
            rows = frame.rows
            if key == "score":
                best = sorted(rows, key=lambda row: row.score, reverse=True)[:5]
                print(f"[{preset.name}] 상위 5: " + ", ".join(f"{r.instrument_id} {r.score:+.4f}" for r in best))
            elif key == "condition":
                true = sorted(r.instrument_id for r in rows if r.matched)
                print(f"[{preset.name}] 참 {len(true)}/{len(rows)}  예 {true[:10]}")
            else:
                print(f"[{preset.name}] {key} 이벤트 {len(rows)}  {[r.instrument_id for r in rows][:10]}")
        for warning in out.warnings:
            print(f"  경고: {warning}")


if __name__ == "__main__":
    main()
