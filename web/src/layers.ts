import type { BlockFunction, GraphEdge, Port, Stage } from "./types";

/** 캔버스 띠는 Python 카탈로그의 stages를 그대로 쓴다. 블록의 띠는 role에서 정해진다. */
export function stageIndex(stages: Stage[], fn: BlockFunction | undefined) {
  if (!fn) return 0;
  const index = stages.findIndex((stage) => stage.id === fn.stage);
  return index === -1 ? 0 : index;
}

export function acceptedTypes(port: Port) {
  return port.accepts.length ? port.accepts : [port.type];
}

/** 출력 포트의 실제 타입. mirrors 포트는 연결된 입력을 따라가 정한다. 정할 수 없으면 null. */
export function resolveOutputType(
  nodeId: string,
  port: Port,
  edges: GraphEdge[],
  fnOf: (nodeId: string) => BlockFunction | undefined,
  seen: Set<string> = new Set(),
): string | null {
  if (!port.mirrors) return port.type;
  const key = `${nodeId}:${port.key}`;
  if (seen.has(key)) return null;
  seen.add(key);
  const edge = edges.find((item) => item.to === nodeId && item.toPort === port.mirrors);
  if (!edge) return null;
  const source = fnOf(edge.from)?.outputs.find((item) => item.key === edge.fromPort);
  return source ? resolveOutputType(edge.from, source, edges, fnOf, seen) : null;
}
