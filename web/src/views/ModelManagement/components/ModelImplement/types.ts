/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:24 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:49:24 
 */
/**
 * Type definitions for Model Implementation
 */

import type { ModelListItem } from '../../types'

/**
 * Model list item with API key ID
 */
export interface ModelList extends ModelListItem {
  /** Associated API key ID */
  api_key_id: string;
}

/**
 * Sub-model modal form data
 */
export interface SubModelModalForm {
  /** Model provider */
  provider: string;
  /** Selected API key IDs (nested array for cascader) */
  api_key_ids: string[][];
}

/**
 * Sub-model modal ref interface
 */
export interface SubModelModalRef {
  /** Open modal */
  handleOpen: () => void;
}

/**
 * Sub-model modal props
 */
export interface SubModelModalProps {
  /** Model type filter */
  type?: string;
  /** Callback to update model list */
  refresh?: (vo: ModelList[]) => void;
  /** Existing models grouped by provider */
  groupedByProvider?: Record<string, ModelList[]>
}