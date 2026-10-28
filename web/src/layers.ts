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

/** 같은 띠의 노드 둘 이상을 입력으로 받는 노드(점수 합치기, 조건 묶기, 조건에 따라 나누기)는 입력 노드를 감싸는
 * 묶음 틀로 그린다. 묶음 노드 id → 입력 노드 id. 같은 입력이 여러 묶음에 들어가면 먼저 찾은 묶음만 감싼다. */
export function mergeGroups(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
  inputIndex: (edge: GraphEdge) => number,
): Map<string, string[]> {
  const known = new Set(ids);
  const groups = new Map<string, string[]>();
  const taken = new Set<string>();
  for (const id of ids) {
    const sources = edges
      .filter((edge) => edge.to === id && known.has(edge.from) && bandOf(edge.from) === bandOf(id))
      .sort((a, b) => inputIndex(a) - inputIndex(b))
      .map((edge) => edge.from);
    const members = [...new Set(sources)].filter((source) => !taken.has(source));
    if (members.length < 2) continue;
    members.forEach((member) => taken.add(member));
    groups.set(id, members);
  }
  return groups;
}

/** 띠마다 한 줄로 놓고 노드의 열을 정한다. 선은 위(앞 단계)에서 아래(뒤 단계)로 흐르므로
 * 다른 띠의 앞 노드 바로 아래 열에 놓고, 같은 띠의 앞 노드보다는 오른쪽에 둔다.
 * 원하는 열 = max(앞 노드 열 (+1, 같은 띠일 때)). 그 띠에서 이미 쓴 열이면 오른쪽 빈 열로 민다.
 * 칸이 나뉜 띠(4단계)는 한 번 놓아 본 뒤, 오른쪽 칸 노드가 왼쪽 칸의 마지막 열보다 오른쪽에서
 * 시작하도록 최소 열을 주고 다시 놓는다. 그래도 어긋나면 그 띠의 열을 칸 순서대로 다시 나눈다.
 * 묶음 노드는 자기 열을 갖지 않고 입력 노드를 감싼다. 입력 노드는 열 순서를 지킨 채 이웃한 자리로 모으고,
 * 묶음의 열은 맨 왼쪽 입력의 열이다.
 * 순환이 있으면 남은 노드를 순서대로 붙인다. */
export function layoutColumns(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
  laneOf: (id: string) => number = () => 0,
  groups: Map<string, string[]> = new Map(),
): Map<string, number> {
  const first = placeColumns(ids, bandOf, edges, () => 0, groups);
  const lastOfLane = new Map<string, number>();
  for (const id of ids) {
    if (groups.has(id)) continue;
    const key = `${bandOf(id)}:${laneOf(id)}`;
    lastOfLane.set(key, Math.max(lastOfLane.get(key) ?? -1, first.get(id)!));
  }
  const minColumn = (id: string) => {
    let floor = 0;
    for (let lane = 0; lane < laneOf(id); lane += 1) floor = Math.max(floor, (lastOfLane.get(`${bandOf(id)}:${lane}`) ?? -1) + 1);
    return floor;
  };
  const columns = placeColumns(ids, bandOf, edges, minColumn, groups);

  const memberOf = new Map<string, string>();
  for (const [group, members] of groups) for (const member of members) memberOf.set(member, group);
  const outermost = (id: string) => {
    let top = id;
    while (memberOf.has(top)) top = memberOf.get(top)!;
    return top;
  };
  // 묶음 안 노드는 지금 열 순서를 지킨다(위 띠에서 내려오는 선이 엇갈리지 않게). 입력 자리는 자리 표시가 알린다.
  const leaves = (id: string): string[] => (
    groups.has(id) ? groups.get(id)!.flatMap(leaves).sort((a, b) => columns.get(a)! - columns.get(b)!) : [id]
  );

  const bands = new Map<number, string[]>();
  for (const id of ids) {
    if (!groups.has(id)) bands.set(bandOf(id), [...(bands.get(bandOf(id)) ?? []), id]);
  }
  for (const list of bands.values()) {
    const slots = list.map((id) => columns.get(id)!).sort((a, b) => a - b);
    const byLane = [...list].sort((a, b) => laneOf(a) - laneOf(b) || columns.get(a)! - columns.get(b)!);
    // 묶음의 입력 노드는 맨 앞 입력 자리에서 한꺼번에 나온다.
    const ordered: string[] = [];
    const emitted = new Set<string>();
    for (const id of byLane) {
      for (const leaf of leaves(outermost(id))) {
        if (!emitted.has(leaf) && list.includes(leaf)) {
          emitted.add(leaf);
          ordered.push(leaf);
        }
      }
    }
    ordered.forEach((id, index) => columns.set(id, slots[index]));
  }
  const groupColumn = (id: string): number => Math.min(...groups.get(id)!.map((member) => (groups.has(member) ? groupColumn(member) : columns.get(member)!)));
  for (const id of groups.keys()) columns.set(id, groupColumn(id));
  return columns;
}

function placeColumns(
  ids: string[],
  bandOf: (id: string) => number,
  edges: GraphEdge[],
  minColumn: (id: string) => number,
  groups: Map<string, string[]>,
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
    const members = groups.get(id);
    if (members) {
      // 묶음은 열을 차지하지 않는다. 아래 띠의 노드가 맨 왼쪽 입력 아래에 오도록 그 열을 준다.
      columns.set(id, Math.min(...members.map((member) => columns.get(member) ?? 0)));
      continue;
    }
    const used = taken.get(band) ?? new Set<number>();
    let column = Math.max(minColumn(id), ...incoming.get(id)!.map((from) => (columns.get(from) ?? 0) + (bandOf(from) === band && !groups.has(from) ? 1 : 0)));
    while (used.has(column)) column += 1;
    columns.set(id, column);
    used.add(column);
    taken.set(band, used);
  }
  return columns;
}
