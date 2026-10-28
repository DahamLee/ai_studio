import type { BlockFunction, GraphEdge, Port, Stage } from "./types";

/** 캔버스 띠는 Python 카탈로그의 stages를 그대로 쓴다. 블록의 띠는 role에서 정해진다. */
export function stageIndex(stages: Stage[], fn: BlockFunction | undefined) {
  if (!fn) return 0;
  const index = stages.findIndex((stage) => stage.id === fn.stage);
  return index === -1 ? 0 : index;
}

/** 띠 안의 칸 번호. 칸이 나뉘지 않은 띠는 0이다(4단계: 0 매매 대상 정하기, 1 매매 신호 잡기). */
export function laneIndex(stages: Stage[], fn: BlockFunction | undefined) {
  if (!fn) return 0;
  const lanes = stages.find((stage) => stage.id === fn.stage)?.lanes ?? [];
  const index = lanes.findIndex((lane) => lane.roles.includes(fn.role));
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
 * 칸이 나뉜 띠(4단계)는 한 번 놓아 본 뒤, 오른쪽 칸 노드가 왼쪽 칸의 마지막 열보다 오른쪽에서
 * 시작하도록 최소 열을 주고 다시 놓는다. 그래도 어긋나면 그 띠의 열을 칸 순서대로 다시 나눈다.
 * 순환이 있으면 남은 노드를 순서대로 붙인다. */
export function layoutColumns(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
  laneOf: (id: string) => number = () => 0,
): Map<string, number> {
  const first = placeColumns(ids, bandOf, edges, () => 0);
  const lastOfLane = new Map<string, number>();
  for (const id of ids) {
    const key = `${bandOf(id)}:${laneOf(id)}`;
    lastOfLane.set(key, Math.max(lastOfLane.get(key) ?? -1, first.get(id)!));
  }
  const minColumn = (id: string) => {
    let floor = 0;
    for (let lane = 0; lane < laneOf(id); lane += 1) floor = Math.max(floor, (lastOfLane.get(`${bandOf(id)}:${lane}`) ?? -1) + 1);
    return floor;
  };
  const columns = placeColumns(ids, bandOf, edges, minColumn);
  const members = new Map<number, string[]>();
  for (const id of ids) members.set(bandOf(id), [...(members.get(bandOf(id)) ?? []), id]);
  for (const list of members.values()) {
    const slots = list.map((id) => columns.get(id)!).sort((a, b) => a - b);
    const ordered = [...list].sort((a, b) => laneOf(a) - laneOf(b) || columns.get(a)! - columns.get(b)!);
    ordered.forEach((id, index) => columns.set(id, slots[index]));
  }
  return columns;
}

function placeColumns(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
  minColumn: (id: string) => number,
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
    let column = Math.max(minColumn(id), ...incoming.get(id)!.map((from) => (columns.get(from) ?? 0) + (bandOf(from) === band ? 1 : 0)));
    while (used.has(column)) column += 1;
    columns.set(id, column);
    used.add(column);
    taken.set(band, used);
  }
  return columns;
}
