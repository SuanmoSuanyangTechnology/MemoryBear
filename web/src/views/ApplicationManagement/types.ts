// 应用数据类型
export interface Application {
  id: string;
  workspace_id: string;
  created_by: string;
  name: string;
  description?: string;
  icon?: string;
  icon_type?: string;
  type: 'agent' | 'multi_agent' | 'workflow';
  visibility: string;
  status: string;
  tags: string[];
  current_release_id?: string;
  is_active: boolean;
  is_shared: boolean;
  created_at: number;
  updated_at: number;
}

// 创建表单数据类型
export interface ApplicationModalData {
  name: string;
  type: string;
  description?: string;
  icon: {
    url: string;
    uid: string | number;
  }[];
}

// 定义组件暴露的方法接口
export interface ApplicationModalRef {
  handleOpen: (application?: Application) => void;
}
export interface ModelConfigModalRef {
  handleOpen: (application?: Application) => void;
}
export interface ModelConfigModalData {
  model: string;
  [key: string]: string;
}
export interface AiPromptModalRef {
  handleOpen: (application?: Application) => void;
}
export interface VariableModalRef {
  handleOpen: (application?: Application) => void;
}
export interface VariableModalProps {
  refresh: () => void;
}
export interface VariableEditModalRef {
  handleOpen: (values?: Variable) => void;
}
export interface Variable {
  index?: number;
  type: string;
  key: string;
  name: string;
  maxLength?: number;
  defaultValue?: string;
  options?: string[];
  required: boolean;
  hidden?: boolean;
}
export interface ApiExtensionModalData {
  name: string;
  apiEndpoint: string;
  apiKey: string;
}
export interface ApiExtensionModalRef {
  handleOpen: () => void;
}
