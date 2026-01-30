export interface Query {
  type?: string;
  provider?: string;
  is_active?: boolean;
  is_public?: boolean;
  is_composite?: boolean;
  search?: string;

  pagesize?: number;
  page?: number;
}
export interface DescriptionItem {
  key: string;
  label: string;
  children: string;
}
export interface CompositeModelForm {
  logo?: any;
  name: string;
  type?: string;
  description: string;
  api_key_ids: ModelApiKey[] | string[];
}
export interface GroupModelModalRef {
  handleOpen: (model?: ModelListItem) => void;
}
export interface GroupModelModalProps {
  refresh?: () => void;
}
export interface ModelListDetailRef {
  handleOpen: (vo: ProviderModelItem) => void;
}


export interface ModelApiKey {
  model_name: string;
  description: string | null;
  provider: string;
  api_key: string;
  api_base: string;
  config: any;
  is_active: boolean;
  priority: string;
  id: string;
  usage_count: string;
  last_used_at: number;
  created_at: number;
  updated_at: number;
  model_config_ids: string[];
}
export interface ModelListItem {
  model_name?: string;
  model_config_ids: string[];
  name: string;
  type: string;
  logo: string;
  description: string;
  provider: string;
  config: any;
  is_active: boolean;
  is_public: boolean;
  id: string;
  created_at: number;
  updated_at: number;
  api_keys: ModelApiKey[]
}
export interface ProviderModelItem {
  provider: string;
  logo?: string;
  tags: string[];
  models: ModelListItem[];
}
export interface KeyConfigModalForm {
  provider: string;
  api_key: string;
  api_base: string;
}
export interface KeyConfigModalRef {
  handleOpen: (vo: ProviderModelItem) => void;
}
export interface KeyConfigModalProps {
  refresh?: () => void;
}
export interface MultiKeyForm {
  model_config_id?: string;
  model_name: string;
  provider: string;
  api_key: string;
  api_base: string;
}

export interface MultiKeyConfigModalRef {
  handleOpen: (vo: ModelListItem, provider?: string) => void;
}
export interface MultiKeyConfigModalProps {
  refresh?: () => void;
}


export interface ModelPlaza {
  provider: string;
  models: ModelPlazaItem[];
}
export interface ModelPlazaItem {
  id: string;
  name: string;
  type: string;
  provider: string;
  logo: string;
  description: string;
  is_deprecated: boolean;
  is_official: boolean;
  tags: string[];
  add_count: number;
  is_added: boolean;
}
export interface ModelSquareDetailRef {
  handleOpen: (vo: ModelPlaza) => void;
}
export interface CustomModelForm {
  name: string;
  type?: string;
  provider?: string;
  logo?: any;
  description: string;
  is_official: boolean;
  tags: string[];
}
export interface CustomModelModalRef {
  handleOpen: (vo?: ModelPlazaItem) => void;
}
export interface CustomModelModalProps {
  refresh?: () => void;
}


export interface BaseRef {
  getList: () => void;
}