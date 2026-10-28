"""S&P 500 종목의 분기 재무 예시 데이터를 만든다. 실제 공시값이 아니라 형식과 조인을 맞춰 보기 위한 가짜 값이다.

실행: python scripts/make_sample_fundamentals.py
입력: data/sp500_ohlc_1y.csv (티커 목록과 첫 종가)
출력: data/sp500_fundamentals_sample.csv

한 행 = 한 종목의 한 분기 공시에서 나온 계정 하나 (long 형식).

    symbol | account_code | value | unit | fiscal_period | fiscal_date | upload_date
    AAPL   | ROE          | 31.42 | %    | 2026Q2        | 2026-06-30  | 2026-08-04

- fiscal_date: 공시가 적용되는 회계 기준일(분기 말). 2026Q2 → 2026-06-30.
- upload_date: 그 공시가 실제로 올라온 날. 이날부터 쓸 수 있다.
  분기 보고서는 분기 말 25~45일 뒤, 4분기(연간 보고서)는 45~75일 뒤이고 주말이면 다음 월요일로 민다.

일봉과는 symbol이 같고 upload_date <= date인 가장 최근 공시로 붙인다(as-of 조인).
fiscal_date로 붙이면 아직 공개되지 않은 실적을 미리 보는 미래 참조가 된다.
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OHLC = ROOT / "data" / "sp500_ohlc_1y.csv"
OUT = ROOT / "data" / "sp500_fundamentals_sample.csv"

COLUMNS = ("symbol", "account_code", "value", "unit", "fiscal_period", "fiscal_date", "upload_date")

# 계정코드: (단위, 설명). 금액은 백만 달러, 주식 수는 백만 주. 비율은 퍼센트 값(10%는 10.0).
ACCOUNTS = {
    "REVENUE": ("USD_M", "매출액(분기)"),
    "NET_INCOME": ("USD_M", "순이익(분기)"),
    "TOTAL_ASSETS": ("USD_M", "자산총계(분기 말)"),
    "TOTAL_LIABILITIES": ("USD_M", "부채총계(분기 말)"),
    "TOTAL_EQUITY": ("USD_M", "자본총계(분기 말)"),
    "SHARES_OUTSTANDING": ("SHARES_M", "발행주식수(분기 말)"),
    "EPS": ("USD", "주당순이익(분기)"),
    "BPS": ("USD", "주당순자산(분기 말)"),
    "ROE": ("%", "자기자본이익률(최근 4분기 순이익 / 자본총계)"),
    "ROA": ("%", "총자산이익률(최근 4분기 순이익 / 자산총계)"),
    "DEBT_RATIO": ("%", "부채비율(부채총계 / 자본총계)"),
}

FIRST_OUTPUT = (2024, 3)  # 일봉 시작(2025-09-30) 1년 전부터 남겨 최근 4분기 값을 바로 쓸 수 있게 한다.
LAST_OUTPUT = (2026, 2)   # 일봉 끝(2026-09-29) 전에 공시되는 마지막 분기.
WARMUP = 3                # 첫 출력 분기의 최근 4분기 합을 위한 앞 분기 수.


def quarters(first: tuple[int, int], last: tuple[int, int]) -> list[tuple[int, int]]:
    year, quarter = first
    out = []
    while (year, quarter) <= last:
        out.append((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return out


def quarter_end(year: int, quarter: int) -> date:
    return date(year, 12, 31) if quarter == 4 else date(year, quarter * 3 + 1, 1) - timedelta(days=1)


def next_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def first_closes() -> dict[str, float]:
    closes: dict[str, float] = {}
    with OHLC.open(newline="") as file:
        for row in csv.DictReader(file):
            if row["symbol"] not in closes and row["close"]:
                closes[row["symbol"]] = float(row["close"])
    return closes


def company_rows(symbol: str, price: float) -> list[tuple[object, ...]]:
    """한 종목의 분기 재무를 만든다. 순이익은 PER 12~40배에 맞춰 첫 종가와 어울리게 잡는다."""
    rng = random.Random(symbol)
    shares = rng.uniform(150, 3000)
    quarterly_income = price / rng.uniform(12, 40) * shares / 4
    margin = rng.uniform(0.05, 0.25)
    growth = rng.gauss(0.015, 0.02)
    equity = quarterly_income * 4 / rng.uniform(0.08, 0.40)
    leverage = rng.uniform(0.4, 3.0)
    lag_quarter, lag_annual = rng.randint(25, 45), rng.randint(45, 75)

    year, quarter = FIRST_OUTPUT
    for _ in range(WARMUP):
        year, quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    all_quarters = quarters((year, quarter), LAST_OUTPUT)

    incomes: list[float] = []
    rows: list[tuple[object, ...]] = []
    for index, (year, quarter) in enumerate(all_quarters):
        income = quarterly_income * (1 + growth) ** index * rng.uniform(0.92, 1.08)
        if rng.random() < 0.04:  # 가끔 적자 분기
            income = -abs(income) * rng.uniform(0.1, 0.6)
        incomes.append(income)
        revenue = abs(income) / (margin * rng.uniform(0.9, 1.1))
        equity *= 1 + rng.uniform(-0.01, 0.03)
        liabilities = equity * leverage * rng.uniform(0.95, 1.05)
        assets = equity + liabilities
        shares *= rng.uniform(0.995, 1.002)
        if index < WARMUP:
            continue
        trailing = sum(incomes[-4:])
        fiscal = quarter_end(year, quarter)
        upload = next_weekday(fiscal + timedelta(days=lag_annual if quarter == 4 else lag_quarter + rng.randint(-3, 3)))
        values = {
            "REVENUE": round(revenue, 2),
            "NET_INCOME": round(income, 2),
            "TOTAL_ASSETS": round(assets, 2),
            "TOTAL_LIABILITIES": round(liabilities, 2),
            "TOTAL_EQUITY": round(equity, 2),
            "SHARES_OUTSTANDING": round(shares, 2),
            "EPS": round(income / shares, 4),
            "BPS": round(equity / shares, 4),
            "ROE": round(trailing / equity * 100, 2),
            "ROA": round(trailing / assets * 100, 2),
            "DEBT_RATIO": round(liabilities / equity * 100, 2),
        }
        for code, (unit, _) in ACCOUNTS.items():
            rows.append((symbol, code, values[code], unit, f"{year}Q{quarter}", fiscal.isoformat(), upload.isoformat()))
    return rows


def main() -> None:
    closes = first_closes()
    rows = [row for symbol in sorted(closes) for row in company_rows(symbol, closes[symbol])]
    with OUT.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"{OUT.relative_to(ROOT)}: {len(closes)}종목, {len(rows)}행")


if __name__ == "__main__":
    main()
