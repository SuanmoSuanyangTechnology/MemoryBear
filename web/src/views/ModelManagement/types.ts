// 模型表单数据类型
export interface ModelFormData extends ApiKey {
  name: string;
  type: string;
  api_keys: ApiKey;
}

export interface DescriptionItem {
  key: string;
  label: string;
  children: string;
}

// 模型类型定义
export interface Model {
  id: string;
  name: string;
  type: string;
  description?: string;
  config: Record<string, unknown>;
  is_active: boolean;
  is_public: boolean;
  created_at: string | number;
  updated_at: string | number;
  api_keys: ApiKey[];

  // provider: string;
  // temperature: number,
  // topP: number,
  // status: string;
  // vectorDimension: number;
  // batchSize: number;
  // truncateStrategy: string;
  // created: string;
  // updatedAt: string;
  // descriptionItems?: Record<string, unknown>[];
  // basicParameters?: string;
  // normalization?: string;
  // maxInputLength?: number;
  // encodingFormat?: string;
  // enablePooling?: boolean;
  // poolingStrategy?: string;
  // apiKey?: string;
  // apiEndpoint?: string;
  // timeout?: number;
  // autoRetry?: boolean;
  // retryCount?: number;
}
interface ApiKey {
  model_name?: string;
  provider: string;
  api_key?: string;
  api_base?: string;
  config?: Record<string, unknown>;
  is_active?: boolean;
  priority?: string;
  id: string;
  model_config_id?: string;
  usage_count?: string;
  last_used_at?: string | null;
  created_at?: string;
  updated_at?: string;
}
// 定义组件暴露的方法接口
export interface ConfigModalRef {
  handleOpen: (model?: Model) => void;
}
export interface ConfigModalProps {
  refresh?: () => void;
}