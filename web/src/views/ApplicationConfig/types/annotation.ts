/**
 * Annotation related types and modal refs.
 */

export interface AnnotationItem {
  id: string;
  question: string;
  answer: string;
  hit_count: number;
  created_at: number;
  updated_at: number;
}

export interface AnnotationSettingForm {
  /** Similarity threshold */
  similarity_threshold?: number;
  /** Model configuration ID */
  model_config_id?: string;
  /** Whether enabled */
  enabled: 1 | 0;
}

export interface AnnotationSettingModalRef {
  handleOpen: () => void;
}

export interface AnnotationFormModalRef {
  handleOpen: (vo?: AnnotationItem) => void;
}

export interface AnnotationForm {
  question: string;
  answer: string;
}

export interface HitHistoryDetailRef {
  handleOpen: (id: string, annotation_id: string) => void;
}
