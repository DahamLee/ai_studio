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

/** 띠마다 한 줄로 놓되, 모든 선이 왼쪽에서 오른쪽으로 가도록 노드의 열을 정한다.
 * 열 = max(앞 노드 열 + 1, 같은 띠에서 이미 쓴 열 + 1). 순환이 있으면 남은 노드를 끝에 붙인다. */
export function layoutColumns(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
): Map<string, number> {
  const incoming = new Map(ids.map((id) => [id, [] as string[]]));
  for (const edge of edges) {
    if (incoming.has(edge.to) && incoming.has(edge.from)) incoming.get(edge.to)!.push(edge.from);
  }
  const columns = new Map<string, number>();
  const lastInBand = new Map<number, number>();
  const remaining = [...ids];
  while (remaining.length) {
    let index = remaining.findIndex((id) => incoming.get(id)!.every((from) => columns.has(from)));
    if (index === -1) index = 0;
    const [id] = remaining.splice(index, 1);
    const band = bandOf(id);
    const after = Math.max(-1, ...incoming.get(id)!.map((from) => columns.get(from) ?? -1));
    const column = Math.max(after + 1, (lastInBand.get(band) ?? -1) + 1);
    columns.set(id, column);
    lastInBand.set(band, column);
  }
  return columns;
}
