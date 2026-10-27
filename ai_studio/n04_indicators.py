"""원본 시세에서 만드는 파생 지표.

이 함수는 좋고 나쁨이나 진입·매도를 정하지 않는다.
"""

from collections.abc import Sequence


def moving_average(values: Sequence[float], window: int) -> float:
    if window < 1:
        raise ValueError("이동평균 기간은 1 이상이어야 합니다.")
    if len(values) < window:
        raise ValueError("이동평균을 계산할 가격이 부족합니다.")
    span = values[-window:]
    return sum(span) / window


def window_high(values: Sequence[float], window: int) -> float:
    if window < 1:
        raise ValueError("고점 기간은 1 이상이어야 합니다.")
    if len(values) < window:
        raise ValueError("고점을 계산할 가격이 부족합니다.")
    return max(values[-window:])


def rsi(closes: Sequence[float], period: int = 14) -> float:
    if period < 2:
        raise ValueError("RSI 기간은 2 이상이어야 합니다.")
    if len(closes) < period + 1:
        raise ValueError("RSI를 계산할 가격이 부족합니다.")
    gains = 0.0
    losses = 0.0
    for index in range(-period, 0):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    if losses == 0:
        return 100.0
    relative = (gains / period) / (losses / period)
    return 100 - (100 / (1 + relative))


def total_return(closes: Sequence[float], lookback: int, skip: int = 0) -> float:
    """skip개 봉을 뺀 시점의 종가를 그보다 lookback개 앞 종가와 비교한 수익률."""
    if lookback < 1 or skip < 0:
        raise ValueError("수익률 기간은 1 이상, 제외 기간은 0 이상이어야 합니다.")
    end = len(closes) - 1 - skip
    start = end - lookback
    if start < 0:
        raise ValueError("수익률을 계산할 가격이 부족합니다.")
    return closes[end] / closes[start] - 1


def daily_returns(closes: Sequence[float], window: int) -> list[float]:
    if len(closes) < window + 1:
        raise ValueError("수익률을 계산할 가격이 부족합니다.")
    tail = closes[-(window + 1):]
    return [tail[index] / tail[index - 1] - 1 for index in range(1, len(tail))]


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("표준편차에는 값이 두 개 이상 필요합니다.")
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / (len(values) - 1)) ** 0.5
