export interface ToolOption {
  value?: string | number | null;
  label?: React.ReactNode;
  description?: string;
  children?: ToolOption[];
  isLeaf?: boolean;
  method_id?: string;
  operation?: string;
  parameters?: Parameter[];
  tool_id?: string;
  enabled?: boolean;
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
export interface ToolModalRef {
  handleOpen: () => void;
}