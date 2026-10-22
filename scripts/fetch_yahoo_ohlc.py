"""S&P 500 최근 1년 일봉 OHLC를 CSV로 저장한다.

실행: python scripts/fetch_yahoo_ohlc.py
필요 패키지: yfinance pandas lxml
"""

from io import StringIO
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sp500_ohlc_1y.csv"


def sp500_symbols() -> list[str]:
    response = requests.get(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        headers={"User-Agent": "ai-studio-ohlc/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    table = pd.read_html(StringIO(response.text))[0]
    return [symbol.replace(".", "-") for symbol in table["Symbol"].astype(str)]


def main() -> None:
    symbols = sp500_symbols()
    frames: list[pd.DataFrame] = []
    for start in range(0, len(symbols), 50):
        batch = symbols[start : start + 50]
        downloaded = yf.download(
            batch,
            period="1y",
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        for symbol in batch:
            if symbol not in downloaded.columns.get_level_values(0):
                continue
            frame = downloaded[symbol][["Open", "High", "Low", "Close", "Volume"]].dropna(how="any")
            if frame.empty:
                continue
            frame = frame.reset_index()
            frame["symbol"] = symbol
            frames.append(frame)
    table = pd.concat(frames, ignore_index=True)
    date_col = "Date" if "Date" in table.columns else table.columns[0]
    table = table.rename(
        columns={date_col: "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}
    )
    table["date"] = pd.to_datetime(table["date"]).dt.strftime("%Y-%m-%d")
    table = table[["date", "symbol", "open", "high", "low", "close", "volume"]].sort_values(["symbol", "date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(OUT, index=False)
    print(f"{OUT} rows={len(table)} symbols={table['symbol'].nunique()} bytes={OUT.stat().st_size}")


if __name__ == "__main__":
    main()
