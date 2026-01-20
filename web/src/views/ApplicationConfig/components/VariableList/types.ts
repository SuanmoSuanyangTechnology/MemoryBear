export interface Variable {
  index?: number;
  name: string;
  display_name: string;
  type: string;
  required: boolean;
  max_length?: number;
  description?: string;

  key?: string;
  default_value?: string;
  options?: string[];
  api_extension?: string;
  hidden?: boolean;
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