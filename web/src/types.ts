export type Port = {
  key: string;
  label: string;
  type: string;
  typeLabel: string;
  description: string;
  required: boolean;
  multiple: boolean;
  minConnections: number | null;
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

export type BlockFunction = {
  id: string;
  name: string;
  category: string;
  categoryLabel: string;
  summary: string;
  inputs: Port[];
  outputs: Port[];
  params: Param[];
  instance: {
    id: string;
    function_id: string;
    params: Record<string, string | number | boolean>;
  };
};

export type Catalog = { functions: BlockFunction[] };
