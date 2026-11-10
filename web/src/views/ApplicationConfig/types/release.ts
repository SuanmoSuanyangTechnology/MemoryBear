/**
 * Release / version publishing related types.
 */
import type { Config } from './config'

/**
 * Release version data
 */
export interface Release {
  /** Release ID */
  id: string;
  /** Application ID */
  app_id: string;
  /** Version number */
  version: string;
  /** Release notes */
  release_notes: string;
  /** Release name */
  name: string;
  /** Release description */
  description?: string;
  /** Application icon */
  icon: string;
  /** Icon type */
  icon_type?: string;
  /** Application type */
  type: string;
  /** Visibility setting */
  visibility: string;
  /** Configuration snapshot */
  config: Config;
  /** Default model configuration ID */
  default_model_config_id?: string;
  /** Publisher user ID */
  published_by?: string;
  /** Publication timestamp */
  published_at: number;
  /** Publisher name */
  publisher_name?: string;
  /** Whether release is active */
  is_active?: boolean;
  /** Creation timestamp */
  created_at?: number;
  /** Last update timestamp */
  updated_at?: number;
  /** Release status */
  status?: string;
  /** Version name */
  version_name?: string;
  /** Tag key for UI display */
  tagKey: 'current' | 'rolledBack' | 'history';
  /**
   * 功能分类 code 数组，1~5 项，随版本冻结（见方案 §4.5）。元素是英文短码，不是中文名；渲染时按语种取 name / name_en。
   */
  categories: string[];
  /**
   * 使用示例，随版本冻结。**纯字符串数组，按录入顺序返回**，不暴露 order/content 落库结构。
   */
  usage_examples: string[];
  /**
   * 设置步骤，随版本冻结。仅 workflow/pure_workflow 类型使用。
   */
  setup_steps?: string;
}

export interface ReleaseModalData {
  version_name: string
  release_notes: string
  icon?: any;
  name?: string;
  categories?: string[];
  usage_examples?: string[];
  setup_steps?: string;
}

/**
 * Release modal ref methods
 */
export interface ReleaseModalRef {
  /** Open release modal */
  handleOpen: (release?: Release | null) => void;
}

/**
 * Release share modal ref methods
 */
export interface ReleaseShareModalRef {
  /** Open release share modal */
  handleOpen: () => void;
}
