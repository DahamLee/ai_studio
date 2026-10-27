import { useEffect, useMemo, useRef, useState } from "react";
import { acceptedTypes, layoutColumns, resolveOutputType, stageIndex, stageOrderProblem } from "./layers";
import type { BacktestReport, BlockFunction, Catalog, GraphCheck, GraphEdge, Param, ParamValue, Port, Preset, Stage } from "./types";

type CanvasNode = {
  instanceId: string;
  functionId: string;
  params: Record<string, ParamValue>;
  x: number;
  y: number;
};

type PortRef = { node: string; port: string };

const NODE_W = 176;
const NODE_H = 88;
const X0 = 24;
const COL_GAP = 20;
const BAND_MIN = 132;
const BAND_HEAD = 30;

function nextInstanceId(base: string, used: Set<string>) {
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

function baseId(functionId: string) {
  return functionId.replaceAll(".", "_").replaceAll("-", "_");
}

function clampY(index: number, y: number, bandH: number) {
  const top = index * bandH + BAND_HEAD;
  const bottom = (index + 1) * bandH - NODE_H - 8;
  return Math.min(Math.max(top, y), Math.max(top, bottom));
}

function homeY(index: number, bandH: number) {
  const room = bandH - BAND_HEAD - NODE_H - 8;
  return index * bandH + BAND_HEAD + Math.max(0, room / 2);
}

/** 포트는 위(입력)·아래(출력) 가장자리에 놓인다. 노드 왼쪽 끝에서의 거리. */
function portOffset(index: number, count: number) {
  if (count <= 1) return NODE_W / 2;
  return 28 + (index * (NODE_W - 56)) / (count - 1);
}

/** 아래 출력에서 위 입력으로 내려가는 선. */
function curve(x1: number, y1: number, x2: number, y2: number) {
  const bend = Math.max(40, Math.abs(y2 - y1) / 2);
  return `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
}

/** 같은 띠 안의 선은 옆 노드로 가로로 잇는다. */
function sideways(x1: number, y1: number, x2: number, y2: number) {
  const bend = Math.max(24, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
}

function edgeKey(edge: GraphEdge) {
  return `${edge.from}:${edge.fromPort}->${edge.to}:${edge.toPort}`;
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [pending, setPending] = useState<PortRef | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bandH, setBandH] = useState(BAND_MIN);
  const [wall, setWall] = useState<string | null>(null);
  const [check, setCheck] = useState<GraphCheck | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [runParams, setRunParams] = useState<Record<string, ParamValue>>({});
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [reportFor, setReportFor] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const boardRef = useRef<HTMLDivElement>(null);
  const bandRef = useRef(BAND_MIN);
  const dragRef = useRef<{ id: string; dx: number; dy: number; blocked: boolean } | null>(null);

  const stages: Stage[] = catalog?.stages ?? [];
  const byId = useMemo(() => new Map((catalog?.functions ?? []).map((fn) => [fn.id, fn])), [catalog]);
  const fnOfNode = (nodeId: string, list: CanvasNode[] = nodes) => {
    const node = list.find((item) => item.instanceId === nodeId);
    return node ? byId.get(node.functionId) : undefined;
  };
  const indexOf = (functionId: string) => stageIndex(stages, byId.get(functionId));

  useEffect(() => {
    const board = boardRef.current;
    if (!board || !stages.length) return;
    const measure = () => {
      const next = Math.max(BAND_MIN, board.clientHeight / stages.length);
      setBandH((current) => (Math.abs(current - next) < 0.5 ? current : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(board);
    return () => observer.disconnect();
  }, [stages.length]);

  useEffect(() => {
    const previous = bandRef.current;
    if (previous === bandH) return;
    bandRef.current = bandH;
    setNodes((current) => current.map((node) => {
      const index = indexOf(node.functionId);
      const offset = node.y - (index * previous + BAND_HEAD);
      return { ...node, y: clampY(index, index * bandH + BAND_HEAD + offset, bandH) };
    }));
  }, [bandH]);

  useEffect(() => {
    if (!wall) return;
    const timer = window.setTimeout(() => setWall(null), 4000);
    return () => window.clearTimeout(timer);
  }, [wall]);

  useEffect(() => {
    fetch("/catalog.json")
      .then((response) => {
        if (!response.ok) throw new Error("함수 목록을 읽지 못했습니다.");
        return response.json() as Promise<Catalog>;
      })
      .then((data) => {
        setCatalog(data);
        // 날짜는 비워 두면 저장된 일봉 전체 기간이다.
        setRunParams(Object.fromEntries(data.runContext.map((param) => [param.key, param.type === "date" ? "" : param.default ?? ""])));
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "불러오기 실패"));
  }, []);

  useEffect(() => {
    if (!catalog || nodes.length || edges.length) return;
    const compose = catalog.functions.find((fn) => fn.id === "universe.compose");
    if (compose) addFunction(compose.id);
  }, [catalog]);

  const graphJson = useMemo(() => JSON.stringify({
    nodes: nodes.map((node) => ({ id: node.instanceId, function_id: node.functionId, params: node.params })),
    edges,
  }), [nodes, edges]);  // 문자열이라 위치만 바뀐 경우 아래 effect는 다시 돌지 않는다.

  // 그래프가 바뀔 때마다 Python 검증기에 물어본다. 연결 규칙의 최종 판단은 Python 한 곳이다.
  useEffect(() => {
    if (!catalog) return;
    const timer = window.setTimeout(() => {
      const body = graphJson;
      fetch("/api/validate", { method: "POST", headers: { "Content-Type": "application/json" }, body })
        .then((response) => {
          if (!response.ok && response.status !== 500) throw new Error();
          return response.json() as Promise<GraphCheck>;
        })
        .then((result) => { setCheck(result); setCheckError(null); })
        .catch(() => { setCheck(null); setCheckError("검증 서버가 없습니다. npm run dev에서만 검증합니다."); });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [catalog, graphJson]);

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return (catalog?.functions ?? []).filter((fn) => {
      return text === "" || fn.name.toLowerCase().includes(text) || fn.id.toLowerCase().includes(text);
    });
  }, [catalog, query]);

  function outputType(nodeId: string, port: Port, edgeList: GraphEdge[], list: CanvasNode[]) {
    return resolveOutputType(nodeId, port, edgeList, (id) => fnOfNode(id, list));
  }

  /** 앞에 놓인 노드 중 맞는 출력을 가진 가장 최근 노드를 필수 입력에 한 번 이어 준다. 이후에는 사용자가 고친다. */
  function suggestEdges(node: CanvasNode, list: CanvasNode[], edgeList: GraphEdge[]) {
    const spec = byId.get(node.functionId);
    if (!spec) return [];
    const upstream = list.filter((item) => item.instanceId !== node.instanceId).reverse();
    const added: GraphEdge[] = [];
    for (const input of spec.inputs) {
      if (!input.required) continue;
      const want = input.multiple ? Math.max(input.minConnections ?? 1, 1) : 1;
      let got = 0;
      for (const source of upstream) {
        const sourceFn = byId.get(source.functionId);
        if (!sourceFn || stageOrderProblem(stages, sourceFn, spec, input)) continue;
        const output = sourceFn.outputs.find((port) => {
          const type = outputType(source.instanceId, port, edgeList, list);
          return type !== null && acceptedTypes(input).includes(type);
        });
        if (!output) continue;
        added.push({ from: source.instanceId, fromPort: output.key, to: node.instanceId, toPort: input.key });
        got += 1;
        if (got >= want) break;
      }
    }
    return added;
  }

  function placeAt(fn: BlockFunction, list: CanvasNode[], id: string, x?: number, y?: number): CanvasNode {
    const index = indexOf(fn.id);
    const count = list.filter((node) => indexOf(node.functionId) === index).length;
    return {
      instanceId: id,
      functionId: fn.id,
      params: { ...fn.instance.params },
      x: x == null ? X0 + count * (NODE_W + COL_GAP) : Math.max(X0, x),
      y: y == null ? homeY(index, bandRef.current) : clampY(index, y, bandRef.current),
    };
  }

  /** 띠마다 한 줄, 모든 선이 왼쪽에서 오른쪽으로 가도록 다시 놓는다. */
  function arrange(list: CanvasNode[], edgeList: GraphEdge[]) {
    const band = new Map(list.map((node) => [node.instanceId, indexOf(node.functionId)]));
    const columns = layoutColumns(list.map((node) => node.instanceId), (id) => band.get(id) ?? 0, edgeList);
    return list.map((node) => ({
      ...node,
      x: X0 + (columns.get(node.instanceId) ?? 0) * (NODE_W + COL_GAP),
      y: homeY(band.get(node.instanceId) ?? 0, bandRef.current),
    }));
  }

  function addFunction(functionId: string, x?: number, y?: number) {
    const fn = byId.get(functionId) ?? catalog?.functions.find((item) => item.id === functionId);
    if (!fn) return;
    const used = new Set(nodes.map((node) => node.instanceId));
    let node = placeAt(fn, nodes, nextInstanceId(baseId(fn.id), used), x, y);
    const suggested = suggestEdges(node, [...nodes, node], edges);
    if (x == null) {
      // 다른 띠의 앞 노드와 이어지면 그 바로 아래에 놓는다. 그 자리가 차 있으면 기본 위치를 쓴다.
      const band = indexOf(fn.id);
      const above = suggested.map((edge) => nodes.find((item) => item.instanceId === edge.from))
        .find((item) => item && indexOf(item.functionId) < band);
      const free = above && !nodes.some((item) => indexOf(item.functionId) === band && Math.abs(item.x - above.x) < NODE_W);
      if (above && free) node = { ...node, x: above.x };
    }
    setNodes([...nodes, node]);
    setEdges((current) => [...current, ...suggested]);
    setSelectedId(node.instanceId);
  }

  function addPreset(preset: Preset) {
    const used = new Set(nodes.map((node) => node.instanceId));
    const prefix = nextInstanceId(preset.id.replaceAll("-", "_"), used);
    const name = (local: string) => `${prefix}_${local}`;
    const list = [...nodes];
    for (const inner of preset.nodes) {
      const fn = byId.get(inner.function_id);
      if (!fn) continue;
      const node = placeAt(fn, list, name(inner.id));
      list.push({ ...node, params: { ...fn.instance.params, ...inner.params } });
    }
    const inner = preset.edges.map((edge) => ({ ...edge, from: name(edge.from), to: name(edge.to) }));
    const universe = [...nodes].reverse().find((node) => byId.get(node.functionId)?.outputs.some((port) => port.type === "universe"));
    const outer = universe
      ? preset.inputs.map((input) => ({ from: universe.instanceId, fromPort: "universe", to: name(input.node), toPort: input.port }))
      : [];
    if (!universe) setWall(`${preset.name}: 유니버스 노드를 먼저 놓으면 입력이 이어집니다.`);
    const edgeList = [...edges, ...inner, ...outer];
    setNodes(arrange(list, edgeList));
    setEdges(edgeList);
    setSelectedId(name(preset.outputs[0]?.node ?? preset.nodes[0].id));
  }

  function loadExample(key: string) {
    const example = catalog?.examples.find((item) => item.key === key);
    if (!example) return;
    const list: CanvasNode[] = [];
    for (const inner of example.nodes) {
      const fn = byId.get(inner.function_id);
      if (!fn) continue;
      const node = placeAt(fn, list, inner.id);
      list.push({ ...node, params: { ...fn.instance.params, ...inner.params } });
    }
    setNodes(arrange(list, example.edges));
    setEdges(example.edges);
    setPending(null);
    setSelectedId(null);
  }

  function setParam(instanceId: string, key: string, value: ParamValue) {
    setNodes((current) => current.map((node) => (
      node.instanceId === instanceId ? { ...node, params: { ...node.params, [key]: value } } : node
    )));
  }

  function runBacktest() {
    setRunning(true);
    const graph = graphJson;
    fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ graph: JSON.parse(graph), settings: runParams }),
    })
      .then((response) => response.json() as Promise<BacktestReport>)
      .then((result) => { setReport(result); setReportFor(graph); })
      .catch(() => { setReport({ ok: false, reason: "백테스트 서버가 없습니다. npm run dev에서만 실행합니다." }); setReportFor(graph); })
      .finally(() => setRunning(false));
  }

  function removeNode(instanceId: string) {
    setNodes((current) => current.filter((node) => node.instanceId !== instanceId));
    setEdges((current) => current.filter((edge) => edge.from !== instanceId && edge.to !== instanceId));
    setSelectedId((current) => (current === instanceId ? null : current));
    setPending((current) => (current?.node === instanceId ? null : current));
  }

  function onPortClick(node: CanvasNode, port: Port, side: "in" | "out") {
    if (side === "out") {
      setPending({ node: node.instanceId, port: port.key });
      return;
    }
    if (!pending) {
      setWall("먼저 이어 줄 출력 포트(노드 아래 파란 점)를 누르세요.");
      return;
    }
    const sourceFn = fnOfNode(pending.node);
    const sourcePort = sourceFn?.outputs.find((item) => item.key === pending.port);
    if (!sourceFn || !sourcePort) return;
    if (pending.node === node.instanceId) {
      setWall("같은 노드끼리는 이을 수 없습니다.");
      return;
    }
    const type = outputType(pending.node, sourcePort, edges, nodes);
    if (type === null || !acceptedTypes(port).includes(type)) {
      setWall(`${port.label}은 ${acceptedTypes(port).join(", ")}만 받습니다. ${sourcePort.label}은 ${type ?? "미정"}입니다.`);
      return;
    }
    const targetFn = fnOfNode(node.instanceId);
    const problem = targetFn ? stageOrderProblem(stages, sourceFn, targetFn, port) : null;
    if (problem) {
      setWall(problem);
      return;
    }
    const edge = { from: pending.node, fromPort: pending.port, to: node.instanceId, toPort: port.key };
    setEdges((current) => {
      const kept = port.multiple ? current : current.filter((item) => !(item.to === node.instanceId && item.toPort === port.key));
      return kept.some((item) => edgeKey(item) === edgeKey(edge)) ? kept : [...kept, edge];
    });
    setPending(null);
  }

  const selected = nodes.find((node) => node.instanceId === selectedId) ?? null;
  const selectedFn = selected ? byId.get(selected.functionId) : null;
  const boardWidth = Math.max(720, ...nodes.map((node) => node.x + NODE_W + X0));
  const boardHeight = bandH * Math.max(stages.length, 1);

  function onBoardDrop(event: React.DragEvent) {
    event.preventDefault();
    const functionId = event.dataTransfer.getData("application/x-function");
    const board = boardRef.current;
    const fn = byId.get(functionId);
    if (!functionId || !board || !fn) return;
    const rect = board.getBoundingClientRect();
    const x = event.clientX - rect.left + board.scrollLeft - NODE_W / 2;
    const y = event.clientY - rect.top + board.scrollTop - 24;
    const index = indexOf(functionId);
    const inside = y >= index * bandH && y < (index + 1) * bandH;
    if (!inside) setWall(`${fn.name} 블록은 ${stages[index]?.label} 단계에만 놓을 수 있습니다.`);
    addFunction(functionId, x, y);
  }

  function onNodePointerDown(event: React.PointerEvent, node: CanvasNode) {
    if ((event.target as HTMLElement).closest("button")) return;
    const board = boardRef.current;
    if (!board) return;
    const rect = board.getBoundingClientRect();
    dragRef.current = {
      id: node.instanceId,
      dx: event.clientX - rect.left + board.scrollLeft - node.x,
      dy: event.clientY - rect.top + board.scrollTop - node.y,
      blocked: false,
    };
    setSelectedId(node.instanceId);
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  function onNodePointerMove(event: React.PointerEvent) {
    const drag = dragRef.current;
    const board = boardRef.current;
    if (!drag || !board) return;
    const rect = board.getBoundingClientRect();
    const x = Math.max(X0, event.clientX - rect.left + board.scrollLeft - drag.dx);
    const rawY = event.clientY - rect.top + board.scrollTop - drag.dy;
    const dragging = nodes.find((node) => node.instanceId === drag.id);
    const index = dragging ? indexOf(dragging.functionId) : 0;
    const y = dragging ? clampY(index, rawY, bandRef.current) : rawY;
    if (dragging && y !== rawY && !drag.blocked) {
      drag.blocked = true;
      const name = byId.get(dragging.functionId)?.name ?? "이";
      setWall(`${name} 블록은 ${stages[index]?.label} 단계 안에서만 옮길 수 있습니다.`);
    }
    setNodes((current) => current.map((node) => (node.instanceId === drag.id ? { ...node, x, y } : node)));
  }

  const status = checkError
    ? { tone: "idle", text: checkError }
    : !check
      ? { tone: "idle", text: "검사 중…" }
      : !check.ok
        ? { tone: "bad", text: `연결 오류: ${check.reason}` }
        : !check.complete
          ? { tone: "warn", text: `완성 전: ${check.completeReason}` }
          : { tone: "good", text: check.warnings.length ? `실행 가능 · 경고 ${check.warnings.length}` : "실행 가능" };

  return (
    <div className="studio">
      <header className="top">
        <div>
          <p className="eyebrow">워크플로우로 만들기</p>
          <h1>전략 캔버스</h1>
        </div>
        <p>선은 바로 다음 단계로만 이어집니다(점선은 단계를 건너뛰는 보조 입력). 노드 아래 파란 점(출력)을 누른 뒤 다른 노드 위 점(입력)을 누르면 이어지고, 선을 누르면 끊깁니다.</p>
        <button type="button" className="tool" onClick={() => setNodes((current) => arrange(current, edges))}>자동 정렬</button>
        <p className={`check ${status.tone}`} role="status" title={check?.warnings.join("\n")}>{status.text}</p>
        {wall ? <p className="wall-note" role="alert">{wall}</p> : null}
      </header>
      {error ? <p className="error">{error}</p> : null}
      <div className="body">
        <aside className="lib" aria-label="노드 추가">
          <form className="run" onSubmit={(event) => event.preventDefault()}>
            <b>실행 조건</b>
            {(catalog?.runContext ?? []).map((param) => (
              <ParamField
                key={param.key}
                param={param}
                value={runParams[param.key] ?? ""}
                placeholder={param.type === "date" ? "전체 기간" : undefined}
                onChange={(value) => setRunParams((current) => ({ ...current, [param.key]: value }))}
              />
            ))}
            <button
              type="button"
              className="run-go"
              disabled={!check?.complete || running}
              title={check?.complete ? "저장된 일봉으로 백테스트합니다." : "실행 가능한 그래프여야 합니다."}
              onClick={runBacktest}
            >
              {running ? "백테스트 중…" : "백테스트 실행"}
            </button>
            <label>
              <span>예제 불러오기</span>
              <select aria-label="예제 불러오기" value="" onChange={(event) => loadExample(event.target.value)}>
                <option value="">예제 전략 선택…</option>
                {(catalog?.examples ?? []).map((example) => (
                  <option key={example.key} value={example.key}>{example.name}</option>
                ))}
              </select>
            </label>
          </form>
          <div className="lib-h">
            <b>노드 추가</b>
            <small>누르면 그 블록의 단계에 놓이고, 맞는 앞 노드와 한 번 이어집니다.</small>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="노드 검색" aria-label="노드 검색" />
          </div>
          <div className="lib-b">
            {(catalog?.presets.length ?? 0) > 0 && query.trim() === "" ? (
              <section className="lib-g">
                <div className="lib-gh"><span className="stepn">★</span><b>프리셋</b></div>
                {catalog!.presets.map((preset) => (
                  <div key={preset.id} className="lib-it">
                    <button type="button" className="lib-ck" onClick={() => addPreset(preset)}>+</button>
                    <button type="button" className="lib-t" title={preset.summary} onClick={() => addPreset(preset)}>
                      <span>{preset.name}</span>
                      <small>블록 {preset.nodes.length}개</small>
                    </button>
                  </div>
                ))}
              </section>
            ) : null}
            {stages.map((stage, index) => {
              const items = visible.filter((fn) => fn.stage === stage.id);
              if (!items.length) return null;
              const used = nodes.filter((node) => indexOf(node.functionId) === index).length;
              return (
                <section key={stage.id} className="lib-g">
                  <div className="lib-gh">
                    <span className="stepn">{index + 1}</span>
                    <b>{stage.label}</b>
                    <small>{used ? `${used}개 사용` : ""}</small>
                  </div>
                  {items.map((fn) => {
                    const count = nodes.filter((node) => node.functionId === fn.id).length;
                    return (
                      <div
                        key={fn.id}
                        className={count ? "lib-it on" : "lib-it"}
                        draggable
                        onDragStart={(event) => {
                          event.dataTransfer.setData("application/x-function", fn.id);
                          event.dataTransfer.effectAllowed = "copy";
                        }}
                      >
                        <button type="button" className="lib-ck" aria-pressed={count > 0} onClick={() => addFunction(fn.id)}>
                          {count ? count : "+"}
                        </button>
                        <button type="button" className="lib-t" title={fn.summary} onClick={() => addFunction(fn.id)}>
                          <span>{fn.name}</span>
                          <small>{fn.id}</small>
                        </button>
                      </div>
                    );
                  })}
                </section>
              );
            })}
          </div>
        </aside>
        <div
          className="board"
          ref={boardRef}
          onDragOver={(event) => event.preventDefault()}
          onDrop={onBoardDrop}
          onClick={(event) => { if (event.target === event.currentTarget) setPending(null); }}
        >
          {catalog ? (
            <div className="board-in" style={{ width: boardWidth, height: boardHeight }}>
              {stages.map((stage, index) => (
                <div
                  key={stage.id}
                  className={index % 2 ? "band alt" : "band"}
                  data-stage={stage.id}
                  style={{ top: index * bandH, height: bandH }}
                >
                  <div className="band-h">
                    <span className="stepn">{index + 1}</span>
                    <b>{stage.label}</b>
                  </div>
                </div>
              ))}
              <svg className="edges" width={boardWidth} height={boardHeight}>
                <defs>
                  <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M0 0 L8 4 L0 8 z" fill="#8fb8e3" />
                  </marker>
                </defs>
                {edges.map((edge) => {
                  const from = nodes.find((node) => node.instanceId === edge.from);
                  const to = nodes.find((node) => node.instanceId === edge.to);
                  const fromFn = from ? byId.get(from.functionId) : undefined;
                  const toFn = to ? byId.get(to.functionId) : undefined;
                  if (!from || !to || !fromFn || !toFn) return null;
                  const outIndex = fromFn.outputs.findIndex((port) => port.key === edge.fromPort);
                  const inIndex = toFn.inputs.findIndex((port) => port.key === edge.toPort);
                  const d = stageIndex(stages, fromFn) === stageIndex(stages, toFn)
                    ? sideways(from.x + NODE_W, from.y + NODE_H / 2, to.x, to.y + NODE_H / 2)
                    : curve(from.x + portOffset(outIndex, fromFn.outputs.length), from.y + NODE_H,
                            to.x + portOffset(inIndex, toFn.inputs.length), to.y);
                  const auxiliary = toFn.inputs[inIndex]?.auxiliary ?? false;
                  return (
                    <path
                      key={edgeKey(edge)}
                      className={auxiliary ? "aux" : undefined}
                      d={d}
                      markerEnd="url(#arrow)"
                      onClick={() => setEdges((current) => current.filter((item) => edgeKey(item) !== edgeKey(edge)))}
                    >
                      <title>{`${edge.from}.${edge.fromPort} → ${edge.to}.${edge.toPort}${auxiliary ? " · 보조 입력" : ""} (눌러서 끊기)`}</title>
                    </path>
                  );
                })}
              </svg>
              {nodes.map((node) => {
                const fn = byId.get(node.functionId);
                if (!fn) return null;
                const walled = dragRef.current?.id === node.instanceId && dragRef.current.blocked;
                const classes = ["cn", node.instanceId === selectedId ? "sel" : "", walled ? "wall" : ""].filter(Boolean).join(" ");
                return (
                  <article
                    key={node.instanceId}
                    role="group"
                    aria-label={fn.name}
                    className={classes}
                    style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                    onPointerDown={(event) => onNodePointerDown(event, node)}
                    onPointerMove={onNodePointerMove}
                    onPointerUp={() => { dragRef.current = null; }}
                  >
                    <Ports ports={fn.inputs} side="in" pending={null} onPick={(port) => onPortClick(node, port, "in")} />
                    <div className="cn-h">
                      <b>{fn.name}</b>
                      <button type="button" aria-label={`${fn.name} 빼기`} onClick={() => removeNode(node.instanceId)}>×</button>
                    </div>
                    <div className="cn-v">{node.instanceId}</div>
                    <Ports
                      ports={fn.outputs}
                      side="out"
                      pending={pending?.node === node.instanceId ? pending.port : null}
                      onPick={(port) => onPortClick(node, port, "out")}
                    />
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>
        <aside className="inspector">
          {report ? <BacktestPanel report={report} stale={reportFor !== graphJson} onClose={() => setReport(null)} /> : null}
          {check?.warnings.length ? (
            <section className="warnings">
              <h3>경고</h3>
              <ul>{check.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </section>
          ) : null}
          {selected && selectedFn ? (
            <>
              <p className="eyebrow">캔버스에 놓인 인스턴스</p>
              <h2>{selectedFn.name}</h2>
              <p className="muted">{stages[indexOf(selected.functionId)]?.label} 단계 · {selectedFn.role}</p>
              <p>{selectedFn.summary}</p>
              {selectedFn.minimumHistory > 1 ? <p className="muted">기본값으로 종목마다 최소 {selectedFn.minimumHistory}개 봉이 필요합니다.</p> : null}
              {selectedFn.params.length ? (
                <>
                  <h3>파라미터</h3>
                  <div className="params">
                    {selectedFn.params.map((param) => (
                      <ParamField
                        key={param.key}
                        param={param}
                        value={selected.params[param.key] ?? param.default ?? ""}
                        onChange={(value) => setParam(selected.instanceId, param.key, value)}
                      />
                    ))}
                  </div>
                </>
              ) : null}
              <h3>입력</h3>
              <ul>
                {selectedFn.inputs.map((port) => {
                  const linked = edges.filter((edge) => edge.to === selected.instanceId && edge.toPort === port.key);
                  return (
                    <li key={port.key}>
                      <code>{port.key}</code>
                      <span className="muted">{acceptedTypes(port).join("·")}{port.required ? "" : " (선택)"}{port.auxiliary ? " · 보조" : ""}</span>
                      <span>{linked.length ? linked.map((edge) => `${edge.from}.${edge.fromPort}`).join(", ") : "—"}</span>
                    </li>
                  );
                })}
                {selectedFn.inputs.length === 0 ? <li className="muted">입력 없음</li> : null}
              </ul>
              <details>
                <summary>인스턴스 JSON</summary>
                <pre>{JSON.stringify({ id: selected.instanceId, function_id: selected.functionId, params: selected.params }, null, 2)}</pre>
              </details>
            </>
          ) : (
            <p className="muted">노드를 누르면 파라미터와 연결이 여기에 나옵니다.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function ParamField({ param, value, placeholder, onChange }: {
  param: Param;
  value: ParamValue;
  placeholder?: string;
  onChange: (value: ParamValue) => void;
}) {
  const title = param.description;
  if (param.type === "boolean") {
    return (
      <label className="pf check-row" title={title}>
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
        <span>{param.label}</span>
      </label>
    );
  }
  let control;
  if (param.type === "enum") {
    control = (
      <select aria-label={param.label} value={String(value)} onChange={(event) => onChange(event.target.value)}>
        {param.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    );
  } else if (param.type === "date") {
    control = (
      <input type="date" aria-label={param.label} value={String(value)} placeholder={placeholder}
             onChange={(event) => onChange(event.target.value)} />
    );
  } else {
    control = (
      <input
        type="number"
        aria-label={param.label}
        min={param.min ?? undefined}
        max={param.max ?? undefined}
        step={param.type === "integer" ? 1 : "any"}
        value={value === "" ? "" : Number(value)}
        onChange={(event) => {
          if (event.target.value === "") return;
          const number = Number(event.target.value);
          onChange(param.type === "integer" ? Math.round(number) : number);
        }}
      />
    );
  }
  return (
    <label className="pf" title={title}>
      <span>{param.label}</span>
      <span className="field">
        {control}
        {param.unit ? <small>{param.unit}</small> : null}
      </span>
      {param.type === "date" && value === "" && placeholder ? <small className="muted">{placeholder}</small> : null}
    </label>
  );
}

const percent = (value: number) => `${value >= 0 ? "+" : ""}${(value * 100).toFixed(2)}%`;

function BacktestPanel({ report, stale, onClose }: { report: BacktestReport; stale: boolean; onClose: () => void }) {
  if (!report.ok) {
    return (
      <section className="result bad">
        <div className="result-h"><h3>백테스트 실패</h3><button type="button" aria-label="결과 닫기" onClick={onClose}>×</button></div>
        <p>{report.reason}</p>
      </section>
    );
  }
  const { summary, equity, trades } = report;
  const values = equity.map((point) => point.value);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const W = 248;
  const H = 72;
  const line = values.map((value, index) => {
    const x = values.length > 1 ? (index * W) / (values.length - 1) : 0;
    const y = high === low ? H / 2 : H - ((value - low) / (high - low)) * H;
    return `${index ? "L" : "M"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
  return (
    <section className="result">
      <div className="result-h">
        <h3>백테스트 결과</h3>
        <button type="button" aria-label="결과 닫기" onClick={onClose}>×</button>
      </div>
      {stale ? <p className="muted">그래프가 바뀌었습니다. 다시 실행하면 갱신됩니다.</p> : null}
      <p className="muted">{summary.start} ~ {summary.end}</p>
      <dl>
        <dt>누적 수익률</dt><dd className={summary.cumulativeReturn >= 0 ? "up" : "down"}>{percent(summary.cumulativeReturn)}</dd>
        <dt>최대 낙폭</dt><dd>{percent(summary.maxDrawdown)}</dd>
        <dt>최종 평가액</dt><dd>{summary.finalValue.toLocaleString(undefined, { maximumFractionDigits: 0 })} USD</dd>
        <dt>체결 / 미체결</dt><dd>{summary.trades} / {summary.skipped}</dd>
      </dl>
      <svg className="equity" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="평가액 추이">
        <path d={line} />
      </svg>
      {trades.length ? (
        <details>
          <summary>최근 체결 {trades.length}건</summary>
          <table>
            <tbody>
              {[...trades].reverse().map((trade, index) => (
                <tr key={index}>
                  <td>{trade.date.slice(5)}</td>
                  <td>{trade.symbol}</td>
                  <td className={trade.side === "BUY" ? "up" : "down"}>{trade.side === "BUY" ? "매수" : "매도"}</td>
                  <td>{trade.quantity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </section>
  );
}

function Ports({ ports, side, pending, onPick }: {
  ports: Port[];
  side: "in" | "out";
  pending: string | null;
  onPick: (port: Port) => void;
}) {
  return (
    <>
      {ports.map((port, index) => (
        <button
          type="button"
          key={`${side}-${port.key}`}
          className={["cport", side, pending === port.key ? "pending" : "", port.required ? "" : "optional"].filter(Boolean).join(" ")}
          title={`${port.label} · ${port.key} · ${acceptedTypes(port).join(", ")}${port.required ? "" : " (선택)"}`}
          aria-label={`${port.label} ${side === "in" ? "입력" : "출력"}`}
          style={{ left: portOffset(index, ports.length) }}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => { event.stopPropagation(); onPick(port); }}
        />
      ))}
    </>
  );
}
