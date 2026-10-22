export type Layer = {
  id: "universe" | "condition" | "signal";
  label: string;
  functionIds: readonly string[];
};

/** 캔버스 층. 블록은 자기 층 안에서만 움직인다. */
export const LAYERS: readonly Layer[] = [
  {
    id: "universe",
    label: "유니버스",
    functionIds: ["universe.compose", "universe.exclude", "universe.min-price"],
  },
  {
    id: "condition",
    label: "조건",
    functionIds: [
      "indicator.rsi",
      "condition.threshold",
      "momentum.trend",
      "momentum.breakout",
      "momentum.factor",
      "factor.value",
      "factor.quality",
      "factor.low-volatility",
      "logic.combine",
      "logic.if-else",
      "logic.set-op",
      "selection.top-n",
      "selection.threshold",
    ],
  },
  {
    id: "signal",
    label: "매수·매도 시그널",
    functionIds: [
      "momentum.ma-cross",
      "strategy.state",
      "strategy.event",
      "strategy.hybrid",
      "allocation.target-weight",
      "allocation.stop",
      "allocation.rebalance",
      "portfolio.max-weight",
      "portfolio.cash",
      "portfolio.target",
    ],
  },
];

export function layerIndex(functionId: string) {
  const index = LAYERS.findIndex((layer) => layer.functionIds.includes(functionId));
  return index === -1 ? 1 : index;
}

export function layerFor(functionId: string) {
  return LAYERS[layerIndex(functionId)];
}
