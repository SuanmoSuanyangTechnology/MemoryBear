/** Knowledge base document v1 API types (mapping to "Design docs / API docs / Knowledge base docs") */

export type DocumentLayout = 'plain' | 'mineru' | 'textln';
export type DocumentChunkingMethod = 'default' | 'custom' | 'parent_child';
export type DocumentParentMode = 'paragraph' | 'full-doc';
export type DispatchStatus = 'submitted' | 'already_processing' | 'dispatch_failed' | 'dispatch_unknown';

export interface DocumentParsing {
  layout?: DocumentLayout;
  html4excel?: boolean;
  follow_hyperlinks?: boolean;
  extract_markdown_hyperlinks?: boolean;
  document_kind?: 'qa' | null;
}

export interface DocumentImageConfig {
  enabled?: boolean;
  mode?: 0 | 1 | 2;
  embedded_enabled?: boolean;
}

export interface DocumentChunkingParentWrite {
  mode?: DocumentParentMode;
  max_tokens?: number;
  delimiters?: string[];
}

export interface DocumentChunkingWrite {
  method?: DocumentChunkingMethod;
  max_tokens?: number;
  overlap_tokens?: number;
  delimiters?: string[];
  auto_keywords?: number;
  parent?: DocumentChunkingParentWrite;
}

export interface DocumentQaWrite {
  questions_per_chunk?: number;
  prompt?: string | null;
}

export interface DocumentMultimodalConfig {
  enabled?: boolean;
  vision_model_id?: string | null;
  audio_model_id?: string | null;
  video_model_id?: string | null;
}

/** Document config write structure; v1 only accepts these five groups */
export interface DocumentProcessingWrite {
  parsing?: DocumentParsing;
  image?: DocumentImageConfig;
  chunking?: DocumentChunkingWrite;
  qa?: DocumentQaWrite;
  multimodal?: DocumentMultimodalConfig;
}

export interface DocumentImageRead {
  enabled: boolean;
  mode: 0 | 1 | 2;
  embedded_enabled: boolean;
}

export interface DocumentChunkingParentRead {
  mode: DocumentParentMode;
  max_tokens: number;
  delimiters: string[];
}

export interface DocumentChunkingRead {
  method: DocumentChunkingMethod;
  max_tokens: number;
  overlap_tokens: number;
  delimiters: string[];
  auto_keywords: number;
  parent: DocumentChunkingParentRead;
}

export interface DocumentQaRead {
  questions_per_chunk: number;
  prompt: string | null;
}

export interface DocumentMultimodalRead {
  enabled: boolean;
  vision_model_id: string | null;
  audio_model_id: string | null;
  video_model_id: string | null;
}

export interface DocumentProcessingRead {
  parsing: DocumentParsing;
  image: DocumentImageRead;
  chunking: DocumentChunkingRead;
  qa: DocumentQaRead;
  multimodal: DocumentMultimodalRead;
}

export interface ProcessingError {
  code: string;
  message: string;
}

export type ProcessingDispatchResult =
  | { status: 'submitted'; task_id: string }
  | { status: 'already_processing'; task_id?: string }
  | { status: 'dispatch_failed'; error: ProcessingError }
  | { status: 'dispatch_unknown'; task_id?: string; error: ProcessingError };

export interface UploadAndParseData {
  file_id: string;
  document_id: string;
  file_saved: boolean;
  processing: ProcessingDispatchResult;
}

export interface RechunkData {
  document_id: string;
  processing: ProcessingDispatchResult;
}

export interface V1DocumentsItem {
  parent_id?: string;
  /**
   * Chunk Num, number of chunks; required.
   */
  chunk_num: number;
  /**
   * Created At, creation time, Unix timestamp in milliseconds; required.
   */
  created_at: number | null;
  /**
   * Created By, creator ID; determined by the auth context at creation; optional, when omitted the previous default or current value is kept.
   */
  created_by: null | string;
  /**
   * File Ext, file extension including the dot, e.g. .pdf; required.
   */
  file_ext: string;
  /**
   * File Id, file ID; must belong to the current knowledge base and be accessible; required.
   */
  file_id: string;
  /**
   * File Meta, file metadata; keys and values depend on the file type; required.
   */
  file_meta: { [key: string]: any };
  /**
   * File Name, file name including the extension; required.
   */
  file_name: string;
  /**
   * File Size, file size in bytes; required.
   */
  file_size: number;
  /**
   * Id, resource ID; required.
   */
  id: string;
  /**
   * Kb Id, knowledge base ID; required.
   */
  kb_id: string;
  /**
   * Parent Child Mode, whether this is a parent-child chunked document, derived from the effective config; optional, when omitted the previous default or current value is kept.
   */
  parent_child_mode: boolean;
  /**
   * Parser Id, parser identifier; keeps the existing value; required.
   */
  parser_id: string;
  /**
   * Process Begin At, processing start time, Unix timestamp in milliseconds; required.
   */
  process_begin_at: number | null;
  /**
   * Process Duration, processing duration in seconds; required.
   */
  process_duration: number;
  processing_configuration: DocumentProcessingRead;
  /**
   * Progress, processing progress; negative values indicate failure, the normal range follows the existing convention; required.
   */
  progress: number;
  /**
   * Progress Msg, processing progress message, may be empty; required.
   */
  progress_msg: null | string;
  /**
   * Run, parsing run flag: 0 not run/finished, 1 running, 2 cancel requested; combine with progress to determine the result.
   */
  run: number;
  /**
   * Status, document validity status: 0 invalid, 1 valid; legacy field, the v2 config PATCH does not accept this field.
   */
  status: number;
  /**
   * Updated At, update time, Unix timestamp in milliseconds; required.
   */
  updated_at: number | null;
  /**
   * Number of associated readable Wiki pages. Returned only when include_wiki_count=true on the admin side; 0 when there are no associated pages.
   */
  wiki_count?: number;
}

export interface V1DocumentCreate {
  kb_id: string;
  file_id: string;
  processing_configuration?: DocumentProcessingWrite;
}

export interface V1DocumentPatch {
  processing_configuration: DocumentProcessingWrite;
}

export interface V1DocumentListQuery {
  /**
   * Whether to sort in descending order.
   */
  desc?: boolean;
  /**
   * Comma-separated UUIDs; limits to the selected documents.
   */
  document_ids?: string;
  /**
   * Keyword, follows the existing search logic.
   */
  keywords?: string;
  /**
   * Sort field, e.g. name/file_name, created_at, updated_at; the original implementation resolves by model property without a hard-coded enum; when omitted no ORDER BY is added and the order is not guaranteed stable.
   */
  orderby?: string;
  /**
   * Page number, starting from 1.
   */
  page?: number;
  /**
   * Page size, up to 100.
   */
  pagesize?: number;
  /**
   * Parent folder ID; omit to not restrict folders.
   */
  parent_id?: string;
  /**
   * Whether to include the number of Wiki pages associated with each document. Defaults to false; wiki_count is returned only when true is passed on the admin side, 0 when there are no associated pages; the public API accepts but ignores this flag and does not return counts.
   */
  include_wiki_count?: boolean;
}

export interface V1DocumentPagination {
  page: number;
  pagesize: number;
  total: number;
  hasnext: boolean;
}

export interface V1DocumentListResponse {
  items: V1DocumentsItem[];
  page: V1DocumentPagination;
}
