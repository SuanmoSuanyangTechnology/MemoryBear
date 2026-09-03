/**
 * Feature / file-upload / content-moderation related configuration types.
 */

export interface FileTypeConfig {
  type: string;
  enabled: boolean;
  maxCount: number;
  maxSize: number;
}

interface FileSettings {
  image_enabled: boolean;
  image_max_size_mb: number;
  image_allowed_extensions: string[];
  audio_enabled: boolean;
  audio_max_size_mb: number;
  audio_allowed_extensions: string[];
  document_enabled: boolean;
  document_max_size_mb: number;
  document_allowed_extensions: string[];
  document_image_recognition?: boolean;
  video_enabled: boolean;
  video_max_size_mb: number;
  video_allowed_extensions: string[];
  max_file_count: number;
  allowed_transfer_methods: string[] | string;
}

export type ModerationType = 'openai' | 'keywords' | 'api';

export interface ContentModerationConfig {
  type: ModerationType;
  config?: {
    keywords?: string;

    api_key?: string;
    api_name?: string;
    api_endpoint?: string;

    inputs_config?: {
      enabled?: boolean;
      preset_response?: string;
    };
    outputs_config?: {
      enabled?: boolean;
      preset_response?: string;
    };
  };
  enabled: boolean;
}

export type FeaturesConfigForm = {
  file_upload: FileSettings & {
    enabled: boolean;
    settings?: FileSettings
  };
  opening_statement: {
    enabled: boolean;
    statement: string | null;
    suggested_questions: string[];
  };
  suggested_questions_after_answer: {
    enabled: boolean;
  };
  text_to_speech: {
    enabled: boolean;
    voice: string | null;
    language: string | null;
    autoplay: boolean;
  };
  citation: {
    enabled: boolean;
  };
  web_search: {
    enabled: boolean;
    search_engine: string | null;
  };
  sensitive_word_avoidance: ContentModerationConfig;
}

/**
 * Function config modal ref methods
 */
export interface FeaturesConfigModalRef {
  /** Open function config modal */
  handleOpen: (value: FeaturesConfigForm) => void;
}
