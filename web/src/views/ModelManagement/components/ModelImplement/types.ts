/**
 * Type definitions for Model Implementation
 */

import type { ModelListItem } from '../../types'

/**
 * Sub-model modal form data
 */
export interface SubModelModalForm {
  /** Model provider */
  provider: string;
  /** Selected API key IDs (nested array for cascader) */
  model_names: string[];
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
  refresh?: (vo: ModelListItem['members']) => void;
  /** Existing models grouped by provider */
  groupedByProvider?: Record<string, ModelListItem['members']>
}