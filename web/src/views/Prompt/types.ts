/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:44:18 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:44:18 
 */
/**
 * Prompt variable modal ref interface
 */
export interface PromptVariableModalRef {
  handleOpen: () => void;
}

/**
 * AI prompt form data
 */
export interface AiPromptForm {
  model_id?: string;
  message?: string;
  current_prompt?: string;
}

/**
 * Prompt release data
 */
export interface PromptReleaseData {
  session_id: string;
  title?: string;
  prompt: string;
}
/**
 * History query parameters
 */
export interface HistoryQuery extends Record<string, unknown> {
  search?: string;
}

/**
 * History item data
 */
export interface HistoryItem {
  id: string;
  title: string;
  prompt: string;
  created_at: number;
  first_message: string;
}

/**
 * Prompt detail modal ref interface
 */
export interface PromptDetailRef {
  handleOpen: (vo: HistoryItem) => void;
  handleClose: () => void;
}

/**
 * Prompt save modal ref interface
 */
export interface PromptSaveModalRef {
  handleOpen: (vo: PromptReleaseData) => void;
}
