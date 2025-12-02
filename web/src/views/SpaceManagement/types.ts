// 应用数据类型
export interface Space {
  id: string;
  name: string;
  description?: string;
  tenant_id: string;
  created_at: string | number;
  is_active: boolean;
  icon: string;
  storage_type: 'rag' | 'neo4j' | null;
}

// 创建表单数据类型
export interface SpaceModalData {
  name: string;
  type: string;
  icon: string;
  llm: string;
  embedding: string;
  rerank: string;
  storage_type: string;
}

// 定义组件暴露的方法接口
export interface SpaceModalRef {
  handleOpen: (space?: Space) => void;
}
export interface ModelConfigModalRef {
  handleOpen: (space?: Space) => void;
}
export interface ModelConfigModalData {
  model: string;
  [key: string]: string;
}
export interface AiPromptModalRef {
  handleOpen: (space?: Space) => void;
}
export interface VariableModalRef {
  handleOpen: (space?: Space) => void;
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
