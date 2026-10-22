"""저장된 일봉 CSV를 호출할 때마다 읽는다."""

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "sp500_ohlc_1y.csv"


@dataclass(frozen=True, slots=True)
class DailyBar:
    date: str
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


def load_ohlc(path: Path = DATA_PATH) -> tuple[DailyBar, ...]:
    with path.open(newline="") as handle:
        return tuple(
            DailyBar(
                row["date"],
                row["symbol"],
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            for row in csv.DictReader(handle)
        )
