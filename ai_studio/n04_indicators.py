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
