import type { ReactNode } from 'react';

import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';

/** Exposes ref capabilities for the parent to pass files, append files, or rechunk an existing document. */
export interface UploadFileModalV1Ref {
  handleOpen: (files: File[], knowledgeBaseId: string, parentId?: string, kbName?: string) => void;
  handleAddFiles: (files: File[]) => void;
  handleRechunk: (document: KnowledgeBaseDocumentData, knowledgeBaseId: string, parentId?: string) => void;
}

export interface UploadFileModalV1Props {
  refresh?: () => void;
  /** When "Add files" is clicked in the modal, request the parent to open the file picker. */
  onRequestFiles?: () => void;
}

export type ConfigSectionKey = 'parsing' | 'chunk' | 'multimodal' | 'advanced';

export interface SelectedFile {
  uid: string;
  name: string;
  size: number;
  originFile: File;
  /** The existing document id in the rechunking scenario. */
  documentId?: string;
  /** The file id when creating a document from an existing file. */
  fileId?: string;
}

/** Display definition of a config section. */
export interface ConfigSection {
  key: ConfigSectionKey;
  labelKey: string;
  descKey: string;
  detailKey: string;
  icon: ReactNode;
}

/** Flat form values of the upload/rechunk modal (corresponding to the old parser_config structure, independent of the new nested form). */
export type UploadFormValues = {
  layout_recognize?: string;
  /** Document type marker; reuse the detail value when rechunking to avoid a QA document degrading to a normal document. */
  document_kind?: 'qa' | null;
  vision_mode?: number;
  chunk_strategy?: ChunkStrategy;
  parent_chunk_mode?: 'paragraph' | 'full-doc';
  parent_chunk_token_num?: number;
  parent_chunk_delimiter?: string[];
  chunk_token_num?: number;
  chunk_overlap?: number;
  delimiter?: string[];
  multimodal_enabled?: boolean;
  image2text_id?: string | null;
  audio2text_id?: string | null;
  video2text_id?: string | null;
  qa_enabled?: boolean;
  auto_questions?: number;
};

export type ChunkStrategy = 'default' | 'parent-child' | 'custom';
