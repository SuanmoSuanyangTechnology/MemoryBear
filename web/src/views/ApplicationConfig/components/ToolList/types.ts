export interface ToolMethod {
  method_id: string;
  operation_key: string | null;
  name: string;
  description: string;
  parameters: Parameter[];
}

export interface Parameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default: any;
  enum: null | string[];
  minimum: number;
  maximum: number;
  pattern: null | string;
}

export interface ToolItem {
  id: string;
  name: string;
  description: string;
  icon: string | null;
  tool_type: 'builtin' | 'custom' | 'mcp' | 'workflow';
  method_count: number;
  is_active: boolean;
}

export interface ToolEntry {
  tool_id: string;
  operation: string | null;
  enabled: boolean;
}

export interface ToolModalRef {
  handleOpen: () => void;
  handleClose: () => void;
}
