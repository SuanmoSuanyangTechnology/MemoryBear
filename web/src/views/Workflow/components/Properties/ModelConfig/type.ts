
/**
 * Model configuration modal ref methods
 */
export interface ModelConfigModalRef {
  /**
   * Open model configuration modal
   * @param source - Configuration source
   * @param model - Optional model data
   */
  handleOpen: (model?: any) => void;
}

interface EnableItem {
  enable: boolean;
  value: number | string | string[];
}
export interface ModelConfigForm {
  model_id?: string;
  capability?: string[];
  temperature?: number;
  max_tokens?: number;
  json_output?: boolean;
  top_p?: EnableItem;
  top_k?: EnableItem;
  seed?: EnableItem;
  repetition_penalty?: EnableItem;
  enable_search?: boolean;
  thinking?: {
    budget: EnableItem,
    enable: boolean
  };
  response_format?: 'text' | 'json_object';
  extra_headers?: EnableItem;
  stop?: EnableItem;
  presence_penalty?: EnableItem;
  frequency_penalty?: EnableItem;
  [key: string]: any;
}