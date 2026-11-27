/** Knowledge base v1 API types (mapping to design docs / API docs / knowledge base) */

export type V1PermissionType = 'Private' | 'Share' | 'Memory';
export type V1KnowledgeType = 'document' | 'Folder';
export type V1Source = 'space' | 'website' | 'third_party';
export type V1IndexStrategy = 'rag' | 'wiki' | 'rag_wiki';

export interface V1FolderPathItem {
  id: string;
  name: string;
}

export interface V1ModelConfigSummary {
  id: string;
  name: string;
  type: 'llm' | 'embedding' | 'rerank' | 'image' | 'video' | 'asr';
  logo?: string | null;
  description?: string | null;
  provider?: string | null;
  /** Admin-side model config without the API key; public responses do not include this field */
  config?: Record<string, unknown> | null;
  is_active?: boolean;
  is_public?: boolean;
  /** Load-balancing strategy */
  load_balance_strategy?: string | null;
  /** Supported input modalities */
  input_modalities?: string[] | null;
  /** Supported output modalities */
  output_modalities?: string[] | null;
  /** Model feature list */
  features?: string[] | null;
  /** Model definition ID */
  model_id?: string | null;
  created_at?: number | null;
  updated_at?: number | null;
  /** Whether the model has been deprecated */
  is_deprecated?: boolean;
}

export interface V1UserSummary {
  id: string;
  username: string;
  email?: string;
  is_active?: boolean;
  is_superuser?: boolean;
  /** Creation time, Unix timestamp in milliseconds */
  created_at?: number;
  /** Last login time, Unix timestamp in milliseconds */
  last_login_at?: number | null;
  current_workspace_id?: string | null;
  current_workspace_name?: string | null;
  role?: string | null;
  preferred_language?: string | null;
  /** Phone number */
  phone?: string | null;
  /** Permission identifier list */
  permissions?: string[] | null;
}
export interface V1SourceWorkspace {
  id: string;
  /**
   * Source space name
   */
  name: null | string;
}

/** List item (knowledge base cards only, excluding Folder) */
export interface V1KnowledgeListItem {
  /**
   * Avatar, avatar URL; optional, when omitted the previous default or current value is kept.
   */
  avatar: null | string;
  /**
   * Chunk Num, number of chunks; optional, when omitted the previous default or current value is kept.
   */
  chunk_num: number | null;
  /**
   * Created At, creation time (Unix milliseconds); for shares this is when the target reference was established / the share was received.
   */
  created_at: number | null;
  /**
   * Created By, creator ID; determined by the auth context at creation; optional, when omitted the previous default or current value is kept.
   */
  created_by: null | string;
  /**
   * Creator user summary; required.
   */
  created_user: V1UserSummary;
  /**
   * Description, description; optional, when omitted the previous default or current value is kept.
   */
  description: null | string;
  /**
   * Doc Num, number of documents; in legacy directory lists it is the subtree document count; optional, when omitted the previous default or current value is kept.
   */
  doc_num: number | null;
  /**
   * Embedding model summary; optional, when omitted the previous default or current value is kept.
   */
  embedding: V1ModelConfigSummary | null;
  /**
   * Embedding Id, embedding model config ID; when omitted or null on create the space default is used, and it fails if there is still no valid model; optional, when omitted the previous default or current value is kept.
   */
  embedding_id: null | string;
  /**
   * External Id, caller-defined identifier, 1-36 characters, unique within the space; optional, when omitted the previous default or current value is kept.
   */
  external_id: null | string;
  /**
   * Path from the root to the containing directory; [] for the root; deleted directories are treated as root.
   */
  folder_path: V1FolderPathItem[];
  /**
   * Id, resource ID; required.
   */
  id: string;
  /**
   * Effective index strategy; shared references read the source library, null for Folder.
   */
  index_strategy: V1IndexStrategy | null;
  /**
   * Whether this is a shared reference received by the current space.
   */
  is_shared: boolean;
  /**
   * LLM summary; optional, when omitted the previous default or current value is kept.
   */
  llm: V1ModelConfigSummary | null;
  /**
   * Llm Id, LLM config ID; follows the existing space default rules; optional, when omitted the previous default or current value is kept.
   */
  llm_id: null | string;
  /**
   * Name, name; required.
   */
  name: string;
  /**
   * Parent Id, parent directory ID; legacy knowledge bases use the space ID for the root; optional, when omitted the previous default or current value is kept.
   */
  parent_id: null | string;
  /**
   * Parser Id, parser identifier; keeps the existing value; optional, when omitted the previous default or current value is kept.
   */
  parser_id: null | string;
  /**
   * Permission type: Private, Share (shared reference), Memory (memory library); optional, when omitted the previous default or current value is kept.
   */
  permission_id: V1PermissionType | null;
  processing_configuration: V1ProcessingConfiguration | null;
  /**
   * Reranker model summary; optional, when omitted the previous default or current value is kept.
   */
  reranker: V1ModelConfigSummary | null;
  /**
   * Reranker Id, reranker model config ID; follows the existing space default rules; optional, when omitted the previous default or current value is kept.
   */
  reranker_id: null | string;
  /**
   * Knowledge base source; null for Folder.
   */
  source: V1Source | null;
  /**
   * Shared source library status: 0 disabled, 1 enabled; null for owned libraries. status is still the target/local status.
   */
  source_status: 0 | 1 | null;
  /**
   * Shared source space; null for owned libraries.
   */
  source_workspace: V1SourceWorkspace | null;
  /**
   * Status, enable/disable of the local library or shared target reference: 0 disabled, 1 enabled; for shared source libraries see source_status.
   */
  status: number;
  /**
   * Content type; Folder for directories.
   */
  type: V1KnowledgeType;
  /**
   * Updated At, update time, Unix timestamp in milliseconds; required.
   */
  updated_at: number | null;
  /**
   * Workspace Id, owning workspace ID; determined by the auth context at creation; optional, when omitted the previous default or current value is kept.
   */
  workspace_id: null | string;
  /**
   * Vision model detail, same structure as llm; corresponds to processing_configuration.multimodal.vision_model_id. null when not set individually or the associated model does not exist;
   * disabling multimodal does not clear the selected model detail. Shared libraries read the source library.
   */
  image2text?: V1ModelConfigSummary | null;
  /**
   * Audio model detail, same structure as llm; corresponds to processing_configuration.multimodal.audio_model_id. null when not set individually or the associated model does not exist;
   * disabling multimodal does not clear the selected model detail. Shared libraries read the source library.
   */
  audio2text?: V1ModelConfigSummary | null;
  /**
   * Video model detail, same structure as llm; corresponds to processing_configuration.multimodal.video_model_id. null when not set individually or the associated model does not exist;
   * disabling multimodal does not clear the selected model detail. Shared libraries read the source library.
   */
  video2text?: V1ModelConfigSummary | null;
}

export interface V1Pagination {
  page: number;
  pagesize: number;
  total: number;
  hasnext: boolean;
}

export interface V1KnowledgeListResponse {
  items: V1KnowledgeListItem[];
  page: V1Pagination;
}

export interface V1ListQuery {
  parent_id?: string;
  page?: number;
  pagesize?: number;
  orderby?: string;
  desc?: boolean;
  keywords?: string;
  kb_ids?: string;
  /**
   * true queries only valid non-directory shares received by the current space; false or omitted queries only owned libraries, including Folder by default.
   */
  is_shared?: boolean;
  /**
   * 0=disabled, 1=enabled; omit to return both. Share filters by the target reference status.
   */
  status?: 0 | 1;
  /**
   * New public types to exclude, comma-separated and case-sensitive: Folder, document.
   */
  exclude_types?: string;
}

/**
 * GET /knowledges/v1/folder-tree current-level directory nodes (lazy-loaded per level, no pagination, no recursion).
 * The outermost all / shared are rendered statically by the front end; the API only returns folders.
 */
export interface V1FolderTreeNode {
  id: string;
  name: string;
  parent_id: string | null;
  knowledge_count: number;
  has_children: boolean;
  /** Only returned in search mode: whether the current directory name matches keywords; false for ancestors that only complete the path */
  is_match?: boolean;
  /** Only returned in search mode: retained sub-directory branches of this search, [] when none */
  children?: V1FolderTreeNode[];
}

export interface V1FolderTreeData {
  folders: V1FolderTreeNode[];
}

export interface V1ImageConfig {
  enabled?: boolean;
  mode?: 0 | 1 | 2;
  embedded_enabled?: boolean;
}

export interface V1ChunkingParent {
  mode?: 'paragraph' | 'full-doc';
  max_tokens?: number;
  delimiters?: string[];
}

export interface V1ChunkingConfig {
  method?: 'default' | 'custom' | 'parent_child';
  max_tokens?: number;
  overlap_tokens?: number;
  delimiters?: string[];
  auto_keywords?: number;
  parent?: V1ChunkingParent;
}

export interface V1QaConfig {
  questions_per_chunk?: number;
  prompt?: string | null;
}

export interface V1DefaultsConfig {
  parsing?: Parsing;
  image?: V1ImageConfig;
  chunking?: V1ChunkingConfig;
  qa?: V1QaConfig;
}

export interface V1MultimodalConfig {
  enabled?: boolean;
  vision_model_id?: string | null;
  audio_model_id?: string | null;
  video_model_id?: string | null;
}

export interface V1GraphConfig {
  enabled?: boolean;
  scene_name?: string;
  entity_types?: string[];
  method?: string;
  resolution?: boolean;
  community?: boolean;
  pipeline?: string;
}

export interface V1WikiConfig {
  language?: string;
  extract_prompt?: string;
}

/** Website source connection config */
export interface V1WebsiteSourceConfig {
  entry_url?: string;
  max_pages?: number;
  delay_seconds?: number;
  timeout_seconds?: number;
  user_agent?: string;
}

/**
 * Yuque source connection config.
 * Write requests use token (writeOnly); read responses do not return credential values and instead use token_set to mark whether it is set.
 */
export interface V1YuqueSourceConfig {
  user_id?: string;
  /** Write request credential; required on create, omitted on PATCH to keep the original value */
  token?: string;
  /** Read response credential flag; does not return the credential value */
  token_set?: boolean;
}

/**
 * Feishu source connection config.
 * Write requests use app_secret / folder_token (writeOnly); read responses use the *_set flags instead.
 */
export interface V1FeishuSourceConfig {
  app_id?: string;
  /** Write request credential; required on create, omitted on PATCH to keep the original value */
  app_secret?: string;
  /** Write request credential; required on create, omitted on PATCH to keep the original value */
  folder_token?: string;
  /** Read response credential flag; does not return the credential value */
  app_secret_set?: boolean;
  /** Read response credential flag; does not return the credential value */
  folder_token_set?: boolean;
}

/** Third-party source connection config */
export interface V1ThirdPartySourceConfig {
  platform?: 'yuque' | 'feishu';
  yuque?: V1YuqueSourceConfig;
  feishu?: V1FeishuSourceConfig;
}

/**
 * Source connection config: empty object for space; website / third_party is one of the two depending on the source.
 */
export interface V1SourcesConfig {
  website?: V1WebsiteSourceConfig;
  third_party?: V1ThirdPartySourceConfig;
}
/**
 * qa config group
 */
export interface Qa {
  /**
   * Auto question generation prompt; null uses the system prompt; always present in responses; returns the actually saved value.
   */
  prompt: null | string;
  /**
   * Number of auto-generated questions per chunk, 0 disables; must be 0 for parent-child chunking and Wiki-only strategies; always present in responses; returns the actually saved value.
   */
  questions_per_chunk: number;
}
/**
 * Chunking method: default system default, custom custom normal chunking, parent_child parent-child chunking; always present in responses; returns the actually saved value.
 */
export enum Method {
    Custom = "custom",
    Default = "default",
    ParentChild = "parent_child",
}

/**
 * Parent block mode: paragraph by paragraph, full-doc full text; always present in responses; returns the actually saved value.
 */
export enum Mode {
    FullDoc = "full-doc",
    Paragraph = "paragraph",
}
/**
 * parent config group
 */
export interface Parent {
  /**
   * Ordered parent block delimiters; always present in responses; returns the actually saved value.
   */
  delimiters: string[];
  /**
   * Maximum token count of a parent block; always present in responses; returns the actually saved value.
   */
  max_tokens: number;
  /**
   * Parent block mode: paragraph by paragraph, full-doc full text; always present in responses; returns the actually saved value.
   */
  mode: Mode;
}
/**
 * chunking config group
 */
export interface Chunking {
  /**
   * Number of auto keywords per chunk, 0 disables; always present in responses; returns the actually saved value.
   */
  auto_keywords: number;
  /**
   * Ordered delimiters; a multi-character value acts as a single delimiter and is converted at runtime by the backtick rules; always present in responses; returns the actually saved value.
   */
  delimiters: string[];
  /**
   * Maximum token count of a child/normal block; always present in responses; returns the actually saved value.
   */
  max_tokens: number;
  /**
   * Chunking method: default system default, custom custom normal chunking, parent_child parent-child chunking; always present in responses; returns the actually saved value.
   */
  method: Method;
  /**
   * Overlap token count between adjacent chunks; always present in responses; returns the actually saved value.
   */
  overlap_tokens: number;
  /**
   * parent config group
   */
  parent: Parent;
}

/**
 * image config group
 */
export interface Image {
    /**
     * Whether to process images embedded in documents; always present in responses; returns the actually saved value.
     */
    embedded_enabled: boolean;
    /**
     * Whether to process directly uploaded images; always present in responses; returns the actually saved value.
     */
    enabled: boolean;
    /**
     * Image parsing mode: 0 OCR text image, 1 OCR + image mixed, 2 pure image; always present in responses; returns the actually saved value.
     */
    mode: number;
}


export enum DocumentKind {
    Qa = "qa",
}

/**
 * PDF/document layout parsing mode; always present in responses; returns the actually saved value.
 */
export type Layout = 'mineru' | "plain" | "textln";
/**
 * parsing config group
 */
export interface Parsing {
  /**
   * Document type flag; qa for Q&A documents, null for normal documents; always present in responses; returns the actually saved value.
   */
  document_kind?: DocumentKind | null;
  /**
   * Whether to extract Markdown links; the old hyperlink_urls is actually a switch; always present in responses; returns the actually saved value.
   */
  extract_markdown_hyperlinks?: boolean;
  /**
   * Whether to parse hyperlink sources inside documents; always present in responses; returns the actually saved value.
   */
  follow_hyperlinks?: boolean;
  /**
   * Whether tables are converted to HTML; always present in responses; returns the actually saved value.
   */
  html4excel?: boolean;
  /**
   * PDF/document layout parsing mode; always present in responses; returns the actually saved value.
   */
  layout?: Layout;
}

/**
 * Layered knowledge base config (v2 structure): multimodal + defaults (parsing/image/chunking/qa) + graph + wiki + sources.
 * Isomorphic with the processing_configuration of list / detail / config-option responses and create / partial-update requests.
 */
export interface V1ProcessingConfiguration {
  /** Multimodal config */
  multimodal?: V1MultimodalConfig;
  /** Default parsing / image / chunking / QA config groups */
  defaults?: V1DefaultsConfig;
  /** Graph config */
  graph?: V1GraphConfig;
  /** Wiki config */
  wiki?: V1WikiConfig;
  /** Source connection config */
  sources?: V1SourcesConfig;
}

export interface V1KnowledgeCreate {
  name: string;
  type: 'document';
  source: V1Source;
  index_strategy: V1IndexStrategy;
  processing_configuration: V1ProcessingConfiguration;
  parent_id?: string | null;
  description?: string | null;
  avatar?: string | null;
  parser_id?: string | null;
  permission_id?: V1PermissionType | null;
  embedding_id?: string | null;
  reranker_id?: string | null;
  llm_id?: string | null;
  external_id?: string | null;
}

/** Folder variant of POST /knowledges/v1 (no model required, no config table written) */
export interface V1FolderCreate {
  name: string;
  type: 'Folder';
  parent_id?: string | null;
  description?: string | null;
  avatar?: string | null;
}

export type V1KnowledgePatch = Partial<Pick<
  V1KnowledgeListItem,
  | 'parent_id' | 'name' | 'description' | 'avatar' | 'permission_id'
  | 'embedding_id' | 'reranker_id' | 'llm_id' | 'parser_id'
>> & {
  processing_configuration?: V1ProcessingConfiguration;

  /**
   * 0 disabled, 1 enabled.
   */
  status?: number;
};

/** Space default model config IDs for GET /knowledges/v1/configuration-options */
export interface V1WorkspaceModels {
  embedding_id: string | null;
  reranker_id: string | null;
  llm_id: string | null;
  vision_model_id: string | null;
  audio_model_id: string | null;
  video_model_id: string | null;
}

/** Configurable field descriptions for GET /knowledges/v1/configuration-options */
export interface V1ConfigurationFieldOption {
  /** Dot-separated field path under processing_configuration */
  path: string;
  /** Scope */
  scope: 'knowledge' | 'document';
  /** JSON type */
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object';
  /** Whether null is allowed */
  nullable: boolean;
  /** Whether unconditionally required on create; conditional requirements are in description/constraints */
  required_on_create: boolean;
  /** Create default value; null when dynamically inherited or no default */
  default: unknown;
  /** Enum values; not returned when there is no enum */
  enum?: unknown[];
  /** Lower bound; not returned when not applicable */
  minimum?: number;
  /** Upper bound; not returned when not applicable */
  maximum?: number;
  /** Chinese field description, conditional requirements and constraints */
  description: string;
}

/** Combination constraints for GET /knowledges/v1/configuration-options */
export interface V1ConfigurationConstraint {
  /** Rule identifier, not equal to a numeric business error code */
  code: string;
  /** Chinese rule description */
  description: string;
  /** Field paths participating in the combination validation */
  fields: string[];
}

/** Response data of GET /knowledges/v1/configuration-options */
export interface V1ConfigurationOptions {
  /** Upload preset for creating a knowledge base (isomorphic with processing_configuration) */
  knowledge_defaults: V1ProcessingConfiguration;
  /** Document processing preset (isomorphic with the defaults group) */
  document_defaults: V1DefaultsConfig;
  /** Space default model config IDs */
  workspace_models: V1WorkspaceModels;
  /** Configurable field descriptions */
  fields: V1ConfigurationFieldOption[];
  /** Combination constraints */
  constraints: V1ConfigurationConstraint[];
}
