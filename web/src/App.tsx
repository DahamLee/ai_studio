import { useEffect, useMemo, useRef, useState } from "react";
import { LAYERS, layerFor, layerIndex } from "./layers";
import type { BlockFunction, Catalog, Port } from "./types";

type CanvasNode = {
  instanceId: string;
  functionId: string;
  params: Record<string, string | number | boolean>;
  x: number;
  y: number;
};

type FlowEdge = {
  from: string;
  fromPort: string;
  to: string;
  toPort: string;
};

const NODE_W = 196;
const NODE_H = 88;
const X0 = 24;
const BAND_MIN = 188;
const BAND_HEAD = 36;

function nextInstanceId(functionId: string, nodes: CanvasNode[]) {
  const base = functionId.replaceAll(".", "_").replaceAll("-", "_");
  const used = new Set(nodes.map((node) => node.instanceId));
  if (!used.has(base)) return base;
  let index = 2;
  while (used.has(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

function connectAll(nodes: CanvasNode[], byId: Map<string, BlockFunction>): FlowEdge[] {
  const edges: FlowEdge[] = [];
  nodes.forEach((node, index) => {
    const spec = byId.get(node.functionId);
    if (!spec) return;
    const upstream = nodes.slice(0, index).reverse();
    for (const input of spec.inputs) {
      const want = input.multiple ? Math.max(input.minConnections ?? 1, 1) : 1;
      let got = 0;
      for (const source of upstream) {
        const output = byId.get(source.functionId)?.outputs.find((port) => port.type === input.type);
        if (!output) continue;
        edges.push({
          from: source.instanceId,
          fromPort: output.key,
          to: node.instanceId,
          toPort: input.key,
        });
        got += 1;
        if (got >= want) break;
      }
    }
  });
  return edges;
}

function clampY(index: number, y: number, bandH: number) {
  const top = index * bandH + BAND_HEAD;
  const bottom = (index + 1) * bandH - NODE_H - 12;
  return Math.min(Math.max(top, y), Math.max(top, bottom));
}

function homeY(index: number, bandH: number) {
  const room = bandH - BAND_HEAD - NODE_H - 12;
  return index * bandH + BAND_HEAD + Math.max(0, room / 2);
}

function placeNode(fn: BlockFunction, nodes: CanvasNode[], bandH: number, x?: number, y?: number): CanvasNode {
  const index = layerIndex(fn.id);
  const count = nodes.filter((node) => layerIndex(node.functionId) === index).length;
  return {
    instanceId: nextInstanceId(fn.id, nodes),
    functionId: fn.id,
    params: { ...fn.instance.params },
    x: x == null ? X0 + count * (NODE_W + 36) : Math.max(X0, x),
    y: y == null ? homeY(index, bandH) : clampY(index, y, bandH),
  };
}

function portOffset(index: number, count: number) {
  if (count <= 1) return NODE_H / 2;
  return 18 + (index * (NODE_H - 36)) / (count - 1);
}

function curve(x1: number, y1: number, x2: number, y2: number) {
  const bend = Math.max(36, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
}

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [bandH, setBandH] = useState(BAND_MIN);
  const [wall, setWall] = useState<string | null>(null);
  const [capital, setCapital] = useState(10000);
  const [feeRate, setFeeRate] = useState(0.1);
  const [slippageRate, setSlippageRate] = useState(0.05);
  const boardRef = useRef<HTMLDivElement>(null);
  const bandRef = useRef(BAND_MIN);
  const dragRef = useRef<{ id: string; dx: number; dy: number; blocked: boolean } | null>(null);

  useEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    const measure = () => {
      const next = Math.max(BAND_MIN, board.clientHeight / LAYERS.length);
      setBandH((current) => (Math.abs(current - next) < 0.5 ? current : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(board);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const previous = bandRef.current;
    if (previous === bandH) return;
    bandRef.current = bandH;
    setNodes((current) => current.map((node) => {
      const index = layerIndex(node.functionId);
      const inner = previous - BAND_HEAD - NODE_H - 12;
      const offset = node.y - (index * previous + BAND_HEAD);
      const nextInner = bandH - BAND_HEAD - NODE_H - 12;
      const scaled = inner <= 0 || nextInner <= 0 ? 0 : (offset / inner) * nextInner;
      return { ...node, y: clampY(index, index * bandH + BAND_HEAD + scaled, bandH) };
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
        const compose = data.functions.find((fn) => fn.id === "universe.compose");
        if (compose) {
          const first = placeNode(compose, [], bandRef.current);
          setNodes([first]);
          setSelectedId(first.instanceId);
        }
        setReady(true);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "불러오기 실패"));
  }, []);

  const byId = useMemo(() => new Map((catalog?.functions ?? []).map((fn) => [fn.id, fn])), [catalog]);
  const edges = useMemo(() => connectAll(nodes, byId), [nodes, byId]);
  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return (catalog?.functions ?? []).filter((fn) => {
      return text === "" || fn.name.toLowerCase().includes(text) || fn.id.toLowerCase().includes(text);
    });
  }, [catalog, query]);

  function addFunction(functionId: string, x?: number, y?: number) {
    const fn = byId.get(functionId);
    if (!fn) return;
    setNodes((current) => {
      const node = placeNode(fn, current, bandRef.current, x, y);
      setSelectedId(node.instanceId);
      return [...current, node];
    });
  }

  function removeNode(instanceId: string) {
    setNodes((current) => current.filter((node) => node.instanceId !== instanceId));
    setSelectedId((current) => (current === instanceId ? null : current));
  }

  const selected = nodes.find((node) => node.instanceId === selectedId) ?? null;
  const selectedFn = selected ? byId.get(selected.functionId) : null;
  const boardWidth = Math.max(720, ...nodes.map((node) => node.x + NODE_W + 80));
  const boardHeight = bandH * LAYERS.length;

  function onBoardDrop(event: React.DragEvent) {
    event.preventDefault();
    const functionId = event.dataTransfer.getData("application/x-function");
    const board = boardRef.current;
    const fn = byId.get(functionId);
    if (!functionId || !board || !fn) return;
    const rect = board.getBoundingClientRect();
    const x = event.clientX - rect.left + board.scrollLeft - NODE_W / 2;
    const y = event.clientY - rect.top + board.scrollTop - 24;
    const index = layerIndex(functionId);
    const inside = y >= index * bandH && y < (index + 1) * bandH;
    if (!inside) setWall(`${fn.name} 블록은 ${layerFor(functionId).label} 층에만 놓을 수 있습니다.`);
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
    const y = dragging ? clampY(layerIndex(dragging.functionId), rawY, bandRef.current) : rawY;
    if (dragging && y !== rawY && !drag.blocked) {
      drag.blocked = true;
      const name = byId.get(dragging.functionId)?.name ?? "이";
      setWall(`${name} 블록은 ${layerFor(dragging.functionId).label} 층 안에서만 옮길 수 있습니다.`);
    }
    setNodes((current) => current.map((node) => (node.instanceId === drag.id ? { ...node, x, y } : node)));
  }

  return (
    <div className="studio">
      <header className="top">
        <div>
          <p className="eyebrow">워크플로우로 만들기</p>
          <h1>빈 워크플로우</h1>
        </div>
        <p>노드는 유니버스, 조건, 매수·매도 시그널 세 층 안에서만 움직입니다. 앞 노드의 출력 타입이 맞으면 선으로 이어집니다.</p>
        {wall ? <p className="wall-note" role="status">{wall}</p> : null}
      </header>
      {error ? <p className="error">{error}</p> : null}
      <div className="body">
        <aside className="lib" aria-label="노드 추가">
          <form className="run" onSubmit={(event) => event.preventDefault()}>
            <b>실행 조건</b>
            <NumberField label="투자금" unit="USD" value={capital} step={1000} onChange={setCapital} />
            <NumberField label="수수료율" unit="%" value={feeRate} step={0.01} onChange={setFeeRate} />
            <NumberField label="슬리피지" unit="%" value={slippageRate} step={0.01} onChange={setSlippageRate} />
          </form>
          <div className="lib-h">
            <b>노드 추가</b>
            <small>누르면 그 블록의 층에 놓입니다.</small>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="노드 검색" aria-label="노드 검색" />
          </div>
          <div className="lib-b">
            {LAYERS.map((layer, index) => {
              const items = layer.functionIds.flatMap((id) => visible.filter((fn) => fn.id === id));
              if (!items.length) return null;
              const used = nodes.filter((node) => layerFor(node.functionId).id === layer.id).length;
              return (
                <section key={layer.id} className="lib-g">
                  <div className="lib-gh">
                    <span className="stepn">{index + 1}</span>
                    <b>{layer.label}</b>
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
                        <button type="button" className="lib-t" onClick={() => addFunction(fn.id)}>
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
        >
          {ready ? (
            <div className="board-in" style={{ width: boardWidth, height: boardHeight }}>
              {LAYERS.map((layer, index) => (
                <div
                  key={layer.id}
                  className={index % 2 ? "band alt" : "band"}
                  data-layer={layer.id}
                  style={{ top: index * bandH, height: bandH }}
                >
                  <div className="band-h">
                    <span className="stepn">{index + 1}</span>
                    <b>{layer.label}</b>
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
                  const x1 = from.x + NODE_W;
                  const y1 = from.y + portOffset(outIndex, fromFn.outputs.length);
                  const x2 = to.x;
                  const y2 = to.y + portOffset(inIndex, toFn.inputs.length);
                  return <path key={`${edge.from}:${edge.fromPort}->${edge.to}:${edge.toPort}`} d={curve(x1, y1, x2, y2)} markerEnd="url(#arrow)" />;
                })}
              </svg>
              {nodes.map((node) => {
                const fn = byId.get(node.functionId);
                if (!fn) return null;
                const walled = dragRef.current?.id === node.instanceId && dragRef.current.blocked;
                return (
                  <article
                    key={node.instanceId}
                    role="group"
                    aria-label={fn.name}
                    className={node.instanceId === selectedId ? (walled ? "cn sel wall" : "cn sel") : "cn"}
                    style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                    onPointerDown={(event) => onNodePointerDown(event, node)}
                    onPointerMove={onNodePointerMove}
                    onPointerUp={() => { dragRef.current = null; }}
                  >
                    <Ports ports={fn.inputs} side="in" />
                    <div className="cn-h">
                      <b>{fn.name}</b>
                      <button type="button" aria-label={`${fn.name} 빼기`} onClick={() => removeNode(node.instanceId)}>×</button>
                    </div>
                    <div className="cn-v">{node.instanceId}</div>
                    <div className="cn-f">{layerFor(node.functionId).label} 층</div>
                    <Ports ports={fn.outputs} side="out" />
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>
        <aside className="inspector">
          {selected && selectedFn ? (
            <>
              <p className="eyebrow">캔버스에 놓인 인스턴스</p>
              <h2>{selectedFn.name}</h2>
              <p className="muted">{layerFor(selected.functionId).label} 층</p>
              <p>{selectedFn.summary}</p>
              <pre>{JSON.stringify({ id: selected.instanceId, function_id: selected.functionId, params: selected.params }, null, 2)}</pre>
              <h3>이 노드에 붙은 선</h3>
              <ul>
                {edges.filter((edge) => edge.from === selected.instanceId || edge.to === selected.instanceId).map((edge) => (
                  <li key={`${edge.from}${edge.fromPort}${edge.to}${edge.toPort}`}>
                    <code>{edge.from}.{edge.fromPort}</code>
                    <span>→</span>
                    <code>{edge.to}.{edge.toPort}</code>
                  </li>
                ))}
                {edges.every((edge) => edge.from !== selected.instanceId && edge.to !== selected.instanceId) ? <li className="muted">아직 이어진 노드가 없습니다.</li> : null}
              </ul>
            </>
          ) : (
            <p className="muted">노드를 누르면 인스턴스 JSON과 연결이 여기에 나옵니다.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function NumberField({ label, unit, value, step, onChange }: {
  label: string;
  unit: string;
  value: number;
  step: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span>{label}</span>
      <span className="field">
        <input
          type="number"
          min={0}
          step={step}
          value={value}
          aria-label={label}
          onChange={(event) => onChange(event.target.value === "" ? 0 : Number(event.target.value))}
        />
        <small>{unit}</small>
      </span>
    </label>
  );
}

function Ports({ ports, side }: { ports: Port[]; side: "in" | "out" }) {
  return (
    <>
      {ports.map((port, index) => (
        <i
          key={`${side}-${port.key}`}
          className={`cport ${side}`}
          title={`${port.label} · ${port.key} · ${port.typeLabel}`}
          style={{ top: portOffset(index, ports.length) }}
        />
      ))}
    </>
  );
}
