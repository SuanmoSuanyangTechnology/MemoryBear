export interface PromptVariableModalRef {
  handleOpen: () => void;
}

export interface AiPromptForm {
  model_id?: string;
  message?: string;
  current_prompt?: string;
}

export interface PromptReleaseData {
  session_id: string;
  title?: string;
  prompt: string;
}
export interface HistoryQuery extends Record<string, unknown> {
  search?: string;
}

export interface HistoryItem {
  id: string;
  title: string;
  prompt: string;
  created_at: number;
  first_message: string;
}

export interface PromptDetailRef {
  handleOpen: (vo: HistoryItem) => void;
  handleClose: () => void;
}

export interface PromptSaveModalRef {
  handleOpen: (vo: PromptReleaseData) => void;
}
