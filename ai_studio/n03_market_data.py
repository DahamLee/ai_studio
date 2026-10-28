"""저장된 일봉 CSV를 MarketBar로 읽고, 거래소 시각을 계산한다.

미국 정규장은 뉴욕 시각 09:30에 열고 16:00에 닫는다. UTC로 고정하면 서머타임에 한 시간씩 어긋나므로
항상 America/New_York에서 계산한다. 휴장일 달력은 없다. 거래일은 데이터에 있는 날짜를 따른다.
"""

import csv
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_studio.n01_frames import MarketBar, StrategyValidationError

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sp500_ohlc_1y.csv"
FUNDAMENTALS_PATH = Path(__file__).resolve().parents[1] / "data" / "sp500_fundamentals_sample.csv"
EXCHANGE_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)

History = Mapping[str, Sequence[MarketBar]]

# 재무 계정코드 → (화면 이름, 단위). 값은 공시된 단위 그대로다. 비율은 퍼센트 값(10%는 10.0).
FUNDAMENTAL_ACCOUNTS: dict[str, tuple[str, str]] = {
    "ROE": ("ROE", "%"),
    "ROA": ("ROA", "%"),
    "DEBT_RATIO": ("부채비율", "%"),
    "EPS": ("주당순이익(분기)", "달러"),
    "BPS": ("주당순자산", "달러"),
    "REVENUE": ("매출액(분기)", "백만 달러"),
    "NET_INCOME": ("순이익(분기)", "백만 달러"),
    "TOTAL_ASSETS": ("자산총계", "백만 달러"),
    "TOTAL_LIABILITIES": ("부채총계", "백만 달러"),
    "TOTAL_EQUITY": ("자본총계", "백만 달러"),
    "SHARES_OUTSTANDING": ("발행주식수", "백만 주"),
}


@dataclass(frozen=True, slots=True)
class Filing:
    """한 종목·한 계정의 분기 공시값. available_at부터 쓸 수 있다."""

    available_at: datetime
    fiscal_period: str
    value: float


# 종목 → 계정코드 → 공개시각 순 공시.
Fundamentals = Mapping[str, Mapping[str, Sequence[Filing]]]


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


def load_fundamentals(path: Path = FUNDAMENTALS_PATH) -> dict[str, dict[str, tuple[Filing, ...]]]:
    """long 형식 재무 CSV(symbol, account_code, value, unit, fiscal_period, fiscal_date, upload_date)를 읽는다.
    업로드일 장 마감에 공개된 것으로 본다. 그날 종가 평가부터 쓰고, 체결은 다음 거래일 시가다.
    회계 기준일(fiscal_date)은 시점 판단에 쓰지 않는다. 쓰면 공개 전 실적을 미리 보게 된다."""
    grouped: dict[str, dict[str, list[Filing]]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            filing = Filing(session_close(row["upload_date"]), row["fiscal_period"], float(row["value"]))
            grouped.setdefault(row["symbol"], {}).setdefault(row["account_code"], []).append(filing)
    return {
        symbol: {code: tuple(sorted(items, key=lambda item: item.available_at)) for code, items in accounts.items()}
        for symbol, accounts in grouped.items()
    }


def filings_until(fundamentals: Fundamentals, symbol: str, account: str, as_of: datetime) -> Sequence[Filing]:
    """as_of까지 공개된 공시. 공개시각 순이다."""
    filings = fundamentals.get(symbol, {}).get(account, ())
    return filings[:bisect_right(filings, as_of, key=lambda item: item.available_at)]


def filing_as_of(fundamentals: Fundamentals, symbol: str, account: str, as_of: datetime) -> Filing | None:
    """as_of까지 공개된 가장 최근 공시. 정정 공시가 나중에 올라오면 그때부터 정정값이다."""
    filings = filings_until(fundamentals, symbol, account, as_of)
    return filings[-1] if filings else None


def trailing_sum(fundamentals: Fundamentals, symbol: str, account: str, as_of: datetime, quarters: int = 4) -> float | None:
    """as_of까지 공개된 최근 quarters개 분기 값의 합(분기 계정의 최근 4분기 합 등).
    같은 분기가 정정되면 나중 공시를 쓴다. 분기가 모자라면 None."""
    latest: dict[str, float] = {}
    for filing in filings_until(fundamentals, symbol, account, as_of):
        latest[filing.fiscal_period] = filing.value
    periods = sorted(latest)[-quarters:]  # "2025Q3" 형식이라 문자열 순서가 분기 순서다.
    return sum(latest[period] for period in periods) if len(periods) == quarters else None


def history_from_bars(bars: Iterable[MarketBar]) -> dict[str, tuple[MarketBar, ...]]:
    """종목별로 묶고 시각순으로 정렬한다. 같은 종목·시각의 봉이 두 번 나오면 데이터 오류다."""
    grouped: dict[str, dict[datetime, MarketBar]] = {}
    for bar in bars:
        stamps = grouped.setdefault(bar.symbol, {})
        if bar.timestamp in stamps:
            raise StrategyValidationError(f"중복 시계열: {bar.symbol} {bar.timestamp}")
        stamps[bar.timestamp] = bar
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
