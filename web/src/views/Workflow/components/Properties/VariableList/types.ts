export interface Variable {
  name: string;
  type: string;
  required: boolean;
  description: string;
  max_length?: number;
  default?: string;
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