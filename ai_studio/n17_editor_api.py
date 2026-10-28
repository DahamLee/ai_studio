"""편집기가 부르는 진입점. 요청 JSON을 표준입력으로 받아 결과 JSON을 표준출력에 쓴다.

검증:     echo '{"nodes": [...], "edges": [...]}' | python -m ai_studio.n17_editor_api
백테스트: echo '{"graph": {...}, "settings": {...}}' | python -m ai_studio.n17_editor_api backtest
웹 개발 서버(vite.config.ts)가 /api/validate, /api/backtest 요청마다 이 모듈을 부른다.
"""

import json
import sys
from collections.abc import Mapping

from ai_studio.n03_market_data import History, history_from_bars, load_ohlc, session_date
from ai_studio.n09_backtest import TRADE_COLUMNS, BacktestResult, trade_rows
from ai_studio.n12_strategies import graph_from_dict
from ai_studio.n13_connect import check_editor_graph
from ai_studio.n15_ohlc_backtest import RunSettings, run_graph_backtest


def backtest(data: Mapping[str, object]) -> dict[str, object]:
    """그래프를 저장된 일봉으로 백테스트하고 편집기에 보여 줄 요약을 돌려준다.
    settings는 RUN_CONTEXT 키(퍼센트 단위)이고, 날짜가 비어 있으면 데이터 전체 기간이다."""
    graph_data = data.get("graph") or {}
    check = check_editor_graph(graph_data)  # type: ignore[arg-type]
    if not check["ok"] or not check["complete"]:
        return {"ok": False, "reason": check["reason"] or check.get("completeReason")}
    try:
        settings = RunSettings.from_params(data.get("settings") or {})  # type: ignore[arg-type]
        history = history_from_bars(load_ohlc())
        book = run_graph_backtest(graph_from_dict(graph_data), history, settings)  # type: ignore[arg-type]
    except FileNotFoundError:
        return {"ok": False, "reason": "저장된 일봉이 없습니다. scripts/fetch_yahoo_ohlc.py로 먼저 받으세요."}
    except ValueError as error:
        return {"ok": False, "reason": str(error)}
    if not book.portfolio_history:
        return {"ok": False, "reason": "선택한 기간에 거래일이 없습니다."}
    points = book.portfolio_history
    return {
        "ok": True,
        "summary": {
            "start": session_date(points[0].as_of).isoformat(),
            "end": session_date(points[-1].as_of).isoformat(),
            "finalValue": points[-1].total_value,
            "cumulativeReturn": points[-1].cumulative_return,
            "maxDrawdown": min(point.drawdown for point in points),
            "trades": len(book.trade_log),
            "skipped": len(book.skipped_orders),
        },
        "equity": [{"date": session_date(p.as_of).isoformat(), "value": p.total_value} for p in points],
        # 매매 내역 전체. 열은 노트북·CSV(write_trades_csv)와 같은 TRADE_COLUMNS다.
        "tradeColumns": list(TRADE_COLUMNS),
        "trades": list(trade_rows(book, initial_cash=settings.initial_capital, ohlc=_fill_day_bars(book, history))),
        "warnings": check["warnings"],
    }


def _fill_day_bars(book: BacktestResult, history: History) -> dict[tuple[str, str], object]:
    """체결일 봉. 매매 내역의 open·high·low·close 열에 쓴다."""
    wanted = {(fill.symbol, session_date(fill.filled_at).isoformat()) for fill in book.trade_log}
    symbols = {symbol for symbol, _ in wanted}
    return {
        key: bar
        for symbol in symbols
        for bar in history.get(symbol, ())
        if (key := (symbol, session_date(bar.timestamp).isoformat())) in wanted
    }


def main() -> None:
    data = json.load(sys.stdin)
    result = backtest(data) if sys.argv[1:] == ["backtest"] else check_editor_graph(data)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
