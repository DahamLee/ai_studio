import type { BlockFunction, GraphEdge, Port, Stage } from "./types";

/** 캔버스 띠는 Python 카탈로그의 stages를 그대로 쓴다. 블록의 띠는 role에서 정해진다. */
export function stageIndex(stages: Stage[], fn: BlockFunction | undefined) {
  if (!fn) return 0;
  const index = stages.findIndex((stage) => stage.id === fn.stage);
  return index === -1 ? 0 : index;
}

/** 선의 단계 규칙. Python check_stage_order와 같다. 통과하면 null, 아니면 사용자에게 보일 이유. */
export function stageOrderProblem(stages: Stage[], from: BlockFunction, to: BlockFunction, port: Port): string | null {
  const source = stageIndex(stages, from);
  const target = stageIndex(stages, to);
  if (target < source) {
    return `${to.name}(${stages[target]?.label})은 뒤 단계인 ${from.name}(${stages[source]?.label})에서 받을 수 없습니다.`;
  }
  if (target - source > 1 && !port.auxiliary) {
    return `${to.name}의 ${port.label}은 바로 앞 단계에서만 받습니다. ${from.name}(${stages[source]?.label})은 단계를 건너뜁니다.`;
  }
  return null;
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

/** 띠마다 한 줄로 놓고 노드의 열을 정한다. 선은 위(앞 단계)에서 아래(뒤 단계)로 흐르므로
 * 다른 띠의 앞 노드 바로 아래 열에 놓고, 같은 띠의 앞 노드보다는 오른쪽에 둔다.
 * 원하는 열 = max(앞 노드 열 (+1, 같은 띠일 때)). 그 띠에서 이미 쓴 열이면 오른쪽 빈 열로 민다.
 * 순환이 있으면 남은 노드를 순서대로 붙인다. */
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
  const taken = new Map<number, Set<number>>();
  const remaining = [...ids];
  while (remaining.length) {
    let index = remaining.findIndex((id) => incoming.get(id)!.every((from) => columns.has(from)));
    if (index === -1) index = 0;
    const [id] = remaining.splice(index, 1);
    const band = bandOf(id);
    const used = taken.get(band) ?? new Set<number>();
    let column = Math.max(0, ...incoming.get(id)!.map((from) => (columns.get(from) ?? 0) + (bandOf(from) === band ? 1 : 0)));
    while (used.has(column)) column += 1;
    columns.set(id, column);
    used.add(column);
    taken.set(band, used);
  }
  return columns;
}
