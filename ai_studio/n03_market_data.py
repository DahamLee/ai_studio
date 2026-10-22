"""저장된 일봉 CSV를 MarketBar로 읽고, 거래소 시각을 계산한다.

미국 정규장은 뉴욕 시각 09:30에 열고 16:00에 닫는다. UTC로 고정하면 서머타임에 한 시간씩 어긋나므로
항상 America/New_York에서 계산한다. 휴장일 달력은 없다. 거래일은 데이터에 있는 날짜를 따른다.
"""

import csv
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_studio.n01_frames import MarketBar

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sp500_ohlc_1y.csv"
EXCHANGE_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)

History = Mapping[str, Sequence[MarketBar]]


def session_open(day: date) -> datetime:
    return datetime.combine(day, SESSION_OPEN, tzinfo=EXCHANGE_TZ)


def session_close(day: date | str) -> datetime:
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return datetime.combine(day, SESSION_CLOSE, tzinfo=EXCHANGE_TZ)


def session_date(moment: datetime) -> date:
    return moment.astimezone(EXCHANGE_TZ).date()


def daily_bar(symbol: str, day: date | str, open: float, high: float, low: float, close: float,
              volume: float = 0.0) -> MarketBar:
    """정규장 일봉. 종가 시각에 공개되고 시가 시각은 같은 날 09:30이다."""
    closed = session_close(day)
    return MarketBar(symbol, closed, open, high, low, close, volume,
                     open_at=session_open(closed.date()))


def load_ohlc(path: Path = DATA_PATH) -> tuple[MarketBar, ...]:
    with path.open(newline="") as handle:
        return tuple(
            daily_bar(
                row["symbol"],
                row["date"],
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            for row in csv.DictReader(handle)
        )


def history_from_bars(bars: Iterable[MarketBar]) -> dict[str, tuple[MarketBar, ...]]:
    """종목별로 묶고 시각순으로 정렬한다. 같은 시각의 봉은 나중 것이 남는다."""
    grouped: dict[str, dict[datetime, MarketBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.symbol, {})[bar.timestamp] = bar
    return {
        symbol: tuple(stamps[stamp] for stamp in sorted(stamps))
        for symbol, stamps in grouped.items()
    }


def bars_until(history: History, symbol: str, as_of: datetime) -> Sequence[MarketBar]:
    """as_of 시점에 공개된 봉까지만. 시계열은 공개시각 순으로 정렬돼 있어야 한다."""
    series = history.get(symbol, ())
    return series[:bisect_right(series, as_of, key=lambda bar: bar.available_at)]


def trading_days(bars: Iterable[MarketBar]) -> tuple[datetime, ...]:
    return tuple(sorted({bar.timestamp for bar in bars}))
