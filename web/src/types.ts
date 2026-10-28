export type Port = {
  key: string;
  label: string;
  type: string;
  typeLabel: string;
  description: string;
  required: boolean;
  multiple: boolean;
  minConnections: number | null;
  /** 실제로 받는 타입. 비어 있으면 type 하나만 받는다. */
  accepts: string[];
  sameTypeAs: string | null;
  /** 출력 타입이 이 입력 포트에 연결된 타입을 따른다. */
  mirrors: string | null;
  /** 보조 입력. 주 입력은 같은 단계나 바로 앞 단계에서만 받지만, 보조 입력은 앞 단계 어디서든 받는다. */
  auxiliary: boolean;
};

export type Param = {
  key: string;
  label: string;
  type: string;
  description: string;
  default: string | number | boolean | null;
  options: { value: string; label: string }[];
  unit: string | null;
  min: number | null;
  max: number | null;
};

export type ParamValue = string | number | boolean;

export type BlockFunction = {
  id: string;
  name: string;
  category: string;
  categoryLabel: string;
  summary: string;
  role: string;
  stage: string;
  version: string;
  needsPrevious: boolean;
  minimumHistory: number;
  inputs: Port[];
  outputs: Port[];
  params: Param[];
  instance: {
    id: string;
    function_id: string;
    params: Record<string, ParamValue>;
  };
};

export type Lane = { label: string; roles: string[] };
export type Stage = { id: string; label: string; roles: string[]; lanes: Lane[] };

export type GraphNode = { id: string; function_id: string; params: Record<string, ParamValue> };
export type GraphEdge = { from: string; fromPort: string; to: string; toPort: string };

export type Preset = {
  id: string;
  name: string;
  summary: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  inputs: { key: string; node: string; port: string }[];
  outputs: { key: string; node: string; port: string }[];
};

export type Example = { key: string; name: string; nodes: GraphNode[]; edges: GraphEdge[] };

export type Catalog = {
  stages: Stage[];
  functions: BlockFunction[];
  presets: Preset[];
  examples: Example[];
  runContext: Param[];
};

/** /api/validate 응답. ok는 연결 규칙, complete는 실행 노드까지 닿는 완성 그래프 여부. */
export type GraphCheck = {
  ok: boolean;
  reason: string | null;
  complete: boolean;
  completeReason?: string | null;
  warnings: string[];
};

/** /api/backtest 응답. 비율은 소수다. */
export type BacktestReport =
  | { ok: false; reason: string | null }
  | {
      ok: true;
      summary: {
        start: string;
        end: string;
        finalValue: number;
        cumulativeReturn: number;
        maxDrawdown: number;
        trades: number;
        skipped: number;
      };
      equity: { date: string; value: number }[];
      /** 매매 내역 전체. 열 순서는 tradeColumns(노트북·CSV와 같은 TRADE_COLUMNS)다. */
      tradeColumns: string[];
      trades: Record<string, string | number | null>[];
      warnings: string[];
    };
