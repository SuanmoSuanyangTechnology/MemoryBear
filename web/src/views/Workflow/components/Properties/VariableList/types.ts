export interface Variable {
  name: string;
  description: string;
  ui_type: string;
  type: string;
  required: boolean;
  max_length?: number;
  default?: string | Record<string, any> | Array<Record<string, any>>;
  options?: string[];
  display_name?: string;
  readonly?: boolean;
  defaultValue?: any;
  value?: any;
}
export interface VariableEditModalRef {
  handleOpen: (values?: Variable) => void;
}

export interface ApiExtensionModalData {
  name: string;
  apiEndpoint: string;
  apiKey: string;
}
export interface ApiExtensionModalRef {
  handleOpen: () => void;
}