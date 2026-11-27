import { type ReactNode } from 'react';
import { type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1';

// 知识库表单数据类型
export interface KnowledgeBaseFormData {
  workspace_id?: string; // 工作空间ID
  id?: string; // 知识库ID 新建时为空
  name?: string; // 知识库名称
  description?: string; // 描述
  avatar?: string; // 头像
  embedding_id?: string | null; // 嵌入模型ID
  llm_id?: string | null; // LLM模型ID
  image2text_id?: string | null; // 图片转文本模型ID
  reranker_id?: string | null; // 重排模型ID
  audio2text_id?: string | null; // 音频模型ID
  video2text_id?: string | null; // 视频模型ID
  chat_id?: string; // 聊天模型ID
  permission_id?: string; // 权限ID
  parent_id?: string; // 父ID
  type?: string; // 知识库类型
  status?: number; // 状态
  parser_config?: ParserConfig; // 解析器配置
}
export interface GraphragConfig{
  use_graphrag:boolean; // 是否启用图谱
  scene_name: string; // 场景名称
  entity_types: Array<string>; // 实体类型
  method: string; // 方法
  resolution: boolean; // 实体归一化
  community: boolean; /// 是否生成社区报告
}
export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  created_by?: string; // 创建者
  created_at?: string;
  updated_at?: string;
  doc_num?: number; // 文档数量(数据集数理)
  chunk_num?: number; // 总数据量
  parser_id?: string; // 解析器ID
  parser_config?: ParserConfig; // 解析器配置
  embedding_id?: string;
  llm_id?: string;
  image2text_id?: string;
  reranker_id?: string;
  audio2text_id?: string;
  video2text_id?: string;
  permission_id?: string;
  type: string;
  status?: number; // 状态 1 启用 0 禁用
  descriptionItems?: Record<string, unknown>[];
}
export interface ModelDisplayItem {
  key: string;
  label: ReactNode;
  disabled?: boolean;
};

export interface ModelMenuInfo {
  menu: ModelDisplayItem[];
};

export interface KnowledgeBaseNavigationState {
  navigateToFolder?: string;
  folderPath?: BreadcrumbItem[];
  resetToRoot?: boolean;
};

export interface RecallTestMetadata {
  doc_id: string;
  file_id: string;
  file_name: string;
  file_created_at: string | number;
  document_id: string;
  knowledge_id: string;
  sort_id: number;
  score: number | null;
  status?: number;

  chunk_type: 'wiki-FAQ',
  faq_id: string;
  wiki_page_id: string;
  question: string;
  answer: string;
  answers: string[];
  answer_strategy: 'all',
  content_revision: number;
}

export interface RecallTestData {
  page_content: string;
  vector: null | number[];
  metadata: RecallTestMetadata;
  children: null | RecallTestData[];
}

export type RetrievalModality = 'text' | 'image';

export type RetrievalPolicy = Partial<Record<string, RetrievalModality[]>>;

export interface RecallTestQuery {
  modality: RetrievalModality;
  content: string;
}

export interface RecallTestParams {
  query: RecallTestQuery; // 多模态查询内容
  kb_ids?: string[]; // 知识库ID
  similarity_threshold?: number; // 相似度阈值
  vector_similarity_weight?: number; //语义相似度权重
  top_k?: number;
  hybrid?: boolean; // 是否混合检索
  hybrid_weight?: string;
  retrieve_type?: string; // 检索类型
  enable_graph_retrieval?: number; // 是否启用图谱检索
  rerank_mode?: 'reranking_model' | 'weighted_score'; // 重排模式
  reranker_id?: string; // 重排模型ID
  rerank_weights?: { semantic_weight: number; participle_weight: number }; // 重排权重
}
// 文件夹 
export interface FolderFormData {
  id?: string; // 文件夹ID 新建时为空
  kb_id: string; // 知识库ID
  parent_id: string; // 父ID 最顶层=知识库id
  parent_name?: string; // 父目录名称
  folder_name?: string; // 文件夹名称
  page?: number;
  pagesize?: number;
  // description: string; // 描述
  createdAt?: string;
  updatedAt?: string;
  file_ext?: 'folder' | string;
}
export interface FileMeta {
  tag: string; // 标签
}
export interface ParserConfig {
  layout_recognize?: string; // 布局识别
  chunk_token_num?: number; // 分块token数量
  delimiter?: string; // 分隔符
  auto_keywords?: number; // 自动关键词
  auto_questions?: number; // 自动问题
  html4excel?: boolean; // 是否为Excel文件
  graphrag?: GraphragConfig; // 知识图谱生成


  parent_chunk_mode?: 'paragraph' | 'full-doc';
  parent_chunk_delimiter?: string;
  parent_chunk_token_num?: number;

  doc_type?: string;
  
  // Web 类型特有字段
  entry_url?: string; // 入口网址
  max_pages?: number; // 最大页面数 (10-200)
  delay_seconds?: number; // 延迟秒数 (1-3)
  timeout_seconds?: number; // 超时秒数 (5-15)
  user_agent?: string; // 用户代理
  
  // Third-party 类型特有字段
  _third_party_platform?: 'yuque' | 'feishu'; // 第三方平台类型
  // 语雀字段
  yuque_user_id?: string; // 语雀用户ID
  yuque_token?: string; // 语雀Token
  // 飞书字段
  feishu_app_id?: string; // 飞书应用ID
  feishu_app_secret?: string; // 飞书应用密钥
  feishu_folder_token?: string; // 飞书文件夹Token

  image?: {
    vision_enabled?: boolean; // 是否启用图片视觉
    vision_mode?: number; // 图片视觉模式 0 文本图片 1 混合图片 2 纯图像
  }

  wiki?: {
    enabled?: boolean;
    language?: 'zh-CN';
    extract_prompt?: string; // 主题抽取关注方向，最多 12000 字符，默认空字符串
  }
}
// 文件数据
export interface KnowledgeBaseDocumentData { // 知识库文档数据
  id?: string; // 文件ID 新建时为空
  file_id?: string; // 文件ID
  kb_id?: string; // 知识库ID
  parent_id?: string; // 文件夹ID
  file_name?: string; // 文件名称
  file_ext?: string; // 文件扩展名
  file_size?: number; // 文件大小
  file_meta?: FileMeta; // 文件元数据
  parser_id?: string; // 解析器ID
  parser_config?: ParserConfig; // 解析器配置
  chunk_num?: number; // 分块数量
  progress?: number; // 进度 1 完成
  progress_msg?: string; // 进度消息
  process_begin_at?: string; // 处理开始时间
  process_duration?: number; // 处理持续时间
  run?: number; // 运行次数
  status?: number; // 状态  1 可检索 0 不可检索
  created_at?: string; // 创建时间
  updated_at?: string; // 更新时间
  qa_prompt?: string; // 提示词
  metadata?: Record<string, any>; // 文档元数据
  parent_child_mode?: boolean; // 是否开启父子模式
  wiki_count?: number; // Number of related Wiki pages (requires include_wiki_count=true)
}
export interface DocumentModalRef {
  handleOpen: (file?: KnowledgeBaseDocumentData | null) => void;
}
export interface DocumentModalRefProps {
  refreshTable?: () => void;
}
export interface KnowledgeBaseFormRef {
  handleOpen: (knowledgeBase?: KnowledgeBase | null) => void;
}

export interface KnowledgeBaseModalRef {
  handleOpen: (knowledgeBase?: KnowledgeBase | null) => void;
}

export interface KnowledgeBaseModalProps {
  refreshTable?: () => void;
}
// 定义组件暴露的方法接口
export interface CreateModalRef {
  handleOpen: (
    knowledgeBaseListItem?: KnowledgeBaseListItem | null,
    type?: string,
    step?: number,
  ) => void;
}
export interface CreateModalRefProps {
  refreshTable?: () => void;
}
export interface RecallTestDrawerRef {
  handleOpen: (knowledgeBaseId?: string) => void;
}

export interface MetadataDrawerRef {
  handleOpen: (knowledgeBaseId: string) => void;
}

export interface MetadataField {
  id: string;
  type: 'string' | 'number' | 'time';
  name: string;
  is_builtin?: boolean;
  count?: number;
  created_at?: number;
  updated_at?: number;
  value?: string | number | null;
}

export interface MetadataModalRef {
  handleOpen: (kbId: string, item?: MetadataField) => void;
}

export interface CreateFolderModalRef {
  handleOpen: (
    folder?: FolderFormData | null,
    type?: string,
    parentPath?: string[],
  ) => void;
}

export interface CreateFolderModalRefProps{
  refreshTable?: () => void;
}

//创建图片数据集 / 创建自定义文本数据集
export interface CreateSetModalRef{
  handleOpen: (kb_id:string, parent_id:string) => void;
}
export interface CreateSetMoealRefProps{
  refreshTable?: () => void;
}

// 创建内容
export interface CreateContentModalRef {
  handleOpen: (kb_id: string, parent_id: string) => void;
}
export interface CreateContentModalRefProps {
  refreshTable?: () => void;
}

// 分享
export interface ShareModalRef {
  handleOpen: (kb_id?: string, knowledgeBase?: V1KnowledgeListItem, spaceIds?:string) => void;
}

export interface ShareModalRefProps {
  handleShare?: (selectedData: { checkedItems: any[], selectedItem: any | null }) => void;
}

// 创建数据集
export interface CreateDatasetModalRef {
  handleOpen: (
    kb_id?: string,
    parent_id?: string,
    documentFolderPath?: Array<{ id: string; name: string }>,
    knowledgeBaseFolderPath?: Array<{ id: string; name: string }>,
  ) => void;
}

export interface CreateDatasetModalRefProps {
  handleCreateDataset?: (payload: { value: number; title: string; description: string }) => void;
}

// ========== API 相关类型 ==========
// 分页请求信息
export interface PageRequest {
  page?: number;
  pagesize?: number;
}
// 分页信息
export interface PageInfo {
  page_num?: number;
  page_size?: number;
  total?: number;
  has_next?: boolean;
}

// 列表查询参数
export interface ListQuery {
  page?: number;
  pagesize?: number;
  orderby?: string;
  desc?: boolean;
  keywords?: string;
  status?: 1 | 0;
  kb_ids?: string;
}

// API Key 信息
export interface ModelAPIKey {
  model_name: string;
  provider: string;
  api_key: string;
  api_base: string;
  config: Record<string, unknown>;
  is_active: boolean;
  priority: string;
  id: string;
  model_config_id: string;
  usage_count: string;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

// 模型信息
export interface Model {
  name: string;
  type: string;
  description: string | null;
  config: Record<string, unknown>;
  is_active: boolean;
  is_public: boolean;
  id: string;
  created_at: string;
  updated_at: string;
}

// 创建用户信息
export interface CreatedUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
}

// 知识库列表项（包含嵌套对象）
export interface KnowledgeBaseListItem extends KnowledgeBase {
  workspace_id: string;
  parent_id: string;
  avatar?: string;
  reranker_id?: string;
  created_user: CreatedUser;
  embedding?: Model;
  reranker?: Model;
  llm?: Model;
  image2text?: Model;
  audio2text?: Model;
  video2text?: Model;
  _expanded?: boolean;
  children?: KnowledgeBaseListItem[];
}

// 知识库列表响应
export interface KnowledgeBaseListResponse {
  items: KnowledgeBaseListItem[];
  page: PageInfo;
}

// 目标空间（分享的目标工作空间）
export interface ShareSpace {
  id: string;
  name: string;
  description?: string;
  tenant_id: string;
  created_at: string;
}

// 分享用户信息
export interface SharedUser {
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 用户当前选中的工作空间UUID，可为null；不代表本次数据已切换到该空间。
   */
  current_workspace_id?: null | string;
  /**
   * 用户当前工作空间名称，可为null。
   */
  current_workspace_name?: null | string;
  /**
   * 用户邮箱，可用于创建者/分享者信息展示。
   */
  email: string;
  /**
   * 用户UUID，表示当前用户摘要对应的身份。
   */
  id: string;
  /**
   * 用户账号是否启用。
   */
  is_active: boolean;
  /**
   * 该用户是否为超级用户的摘要标记；前端不能据此替代服务端权限判断。
   */
  is_superuser: boolean;
  /**
   * 用户最近登录时间，Unix毫秒时间戳；字段允许null时表示尚无记录。
   */
  last_login_at?: number | null;
  /**
   * 用户权限标识数组，可为null；最终可执行操作仍由后端鉴权决定。
   */
  permissions?: string[] | null;
  /**
   * 用户联系电话；无配置时可为null。
   */
  phone?: null | string;
  /**
   * 用户偏好语言，如zh或en；与Wiki生成内容的language配置不同。
   */
  preferred_language?: null | string;
  /**
   * 用户角色标识，可为null，沿用系统既有角色定义。
   */
  role?: null | string;
  /**
   * 用户显示名称或登录名。
   */
  username: string;
}
export interface ShareRequestParams {
  source_kb_id?: string;
  source_workspace_id?: string;
  target_workspace_id?: string;
}
// 知识库分享记录
export interface KnowledgeBaseShare {
  id: string;
  source_kb_id: string;
  source_workspace_id: string;
  target_kb_id: string;
  target_workspace_id: string;
  shared_by: string;
  created_at: string;
  updated_at: string;
  target_kb: KnowledgeBase;
  target_workspace: ShareSpace;
  shared_user: SharedUser;
}

// 知识库分享列表响应
export interface KnowledgeBaseShareListResponse {
  list: KnowledgeBaseShare[];
  page: PageInfo;
}

// 文件上传
export interface UploadFileFormData {
  kb_id?: string;
  parent_id?: string;
  file: File;
}
export interface  UploadFileResponse extends UploadFileFormData{
  id: string;
  file_id: string;
  file_name: string;
  file_size: number;
  file_ext: string;
  file_meta: FileMeta;
  parser_id: string; // 解析器ID
  parser_config: ParserConfig; // 解析器配置
  chunk_num: number; // 分块数量
  progress: number; // 进度 1 完成
  progress_msg: string; // 进度消息
  process_begin_at: string; // 处理开始时间
  process_duration: number; // 处理持续时间
  run: number; // 运行次数
  status: number; // 状态  1 可检索 0 不可检索
  created_at: string;
  updated_at: string;
}
export interface FileMeta {
  tag: string; // 标签
}

export interface PathQuery extends ListQuery {
  kb_id?: string;
  parent_id?: string;
  workspace_id?: string;
  document_id?: string;
  _t?: string | number;
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Audio2Text {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

/**
 * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
 */
export enum Audio2TextType {
  Asr = "asr",
  Embedding = "embedding",
  Image = "image",
  Llm = "llm",
  Rerank = "rerank",
  Video = "video",
}

/**
 * Legacy user response fields required by Knowledge and Share responses.
 */
export interface CreatedUser {
  /**
   * Unix 毫秒时间戳。
   */
  created_at: string;
  /**
   * 用户当前选中的工作空间UUID，可为null；不代表本次数据已切换到该空间。
   */
  current_workspace_id?: null | string;
  /**
   * 用户当前工作空间名称，可为null。
   */
  current_workspace_name?: null | string;
  /**
   * 用户邮箱，可用于创建者/分享者信息展示。
   */
  email: string;
  /**
   * 用户UUID，表示当前用户摘要对应的身份。
   */
  id: string;
  /**
   * 用户账号是否启用。
   */
  is_active: boolean;
  /**
   * 该用户是否为超级用户的摘要标记；前端不能据此替代服务端权限判断。
   */
  is_superuser: boolean;
  /**
   * 用户最近登录时间，Unix毫秒时间戳；字段允许null时表示尚无记录。
   */
  last_login_at?: number | null;
  /**
   * 用户权限标识数组，可为null；最终可执行操作仍由后端鉴权决定。
   */
  permissions?: string[] | null;
  /**
   * 用户联系电话；无配置时可为null。
   */
  phone?: null | string;
  /**
   * 用户偏好语言，如zh或en；与Wiki生成内容的language配置不同。
   */
  preferred_language?: null | string;
  /**
   * 用户角色标识，可为null，沿用系统既有角色定义。
   */
  role?: null | string;
  /**
   * 用户显示名称或登录名。
   */
  username: string;
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Embedding {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Image2Text {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Llm {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

/**
 * 解析配置。包含知识库级 Wiki 设置；读取历史知识库时 wiki 可缺省。
 */
export interface ParserConfig {
  /**
   * 知识库级 Wiki 配置。各子字段可省略；更新时按字段合并，省略字段保留原值。
   */
  wiki?: Wiki;
}

/**
 * 知识库级 Wiki 配置。各子字段可省略；更新时按字段合并，省略字段保留原值。
 */
export interface Wiki {
  /**
   * Wiki 生成开关；新库默认 false。更新为 true 或再次显式提交 true 时触发符合条件文档的补录检查。
   */
  enabled?: boolean;
  /**
   * 主题抽取关注方向；默认空字符串。单独修改本字段不自动重建所有已有页面。
   */
  extract_prompt?: string;
  /**
   * 生成语言。默认 zh-CN；去除首尾空白后不可为空，最长 64 字符。
   */
  language?: 'zh-CN';
}

export enum Permissionid {
  Memory = "Memory",
  Private = "Private",
  Share = "Share",
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Reranker {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

export enum TypeType {
  Folder = "Folder",
  General = "General",
  ThirdParty = "Third-party",
  Web = "Web",
}

/**
 * Legacy model fields without API key objects or credentials.
 */
export interface Video2Text {
  /**
   * 管理端模型配置摘要，结构随模型提供方变化；Wiki页面展示通常无需读取其中配置。
   */
  config?: { [key: string]: any } | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 模型支持的功能特征标识数组，具体取值以当前模型服务为准。
   */
  features?: string[] | null;
  /**
   * 模型配置UUID，区别于模型实体model_id。
   */
  id: string;
  /**
   * 模型允许的输入模态标识数组，例如text/image；可为空，不表示所有Wiki处理流程均已启用。
   */
  input_modalities?: string[] | null;
  /**
   * 模型配置是否启用，不表示Wiki任务已开始或完成。
   */
  is_active?: boolean;
  /**
   * 模型是否已标记废弃；用于模型选择或风险提示。
   */
  is_deprecated?: boolean;
  /**
   * 模型配置是否为公共可见配置的标记；不表示目标知识库或Wiki页面公开。
   */
  is_public?: boolean;
  /**
   * 模型配置的负载均衡策略，沿用模型服务返回的枚举值。
   */
  load_balance_strategy?: null | string;
  /**
   * 模型提供方或模型的图标引用，可为null。
   */
  logo?: null | string;
  /**
   * 关联的模型实体UUID，区别于当前模型配置对象的id。
   */
  model_id?: null | string;
  /**
   * 模型配置名称，供模型信息展示。
   */
  name: string;
  /**
   * 模型允许的输出模态标识数组，例如text；可为空。
   */
  output_modalities?: string[] | null;
  /**
   * 模型提供方标识；用于模型信息展示，不是模型配置UUID。
   */
  provider: string;
  /**
   * 模型用途类型，按当前模型服务契约解释；不等于Wiki页面类型。
   */
  type: Audio2TextType;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

/**
 * 目标工作空间摘要，与当前登录用户的current_workspace_id不同。
 */
export interface TargetWorkspace {
  /**
   * 工作空间默认音频模型配置标识，可为null；不表示当前Wiki流程已支持音频。
   */
  audio?: null | string;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 目标工作空间默认向量模型配置标识，可为null。
   */
  embedding?: null | string;
  /**
   * 工作空间图标引用，可为null。
   */
  icon?: null | string;
  /**
   * 工作空间图标类型，按既有图标渲染规则使用。
   */
  iconType?: null | string;
  /**
   * 工作空间UUID。
   */
  id: string;
  /**
   * 工作空间是否采用默认配置的标记，不是Wiki启用开关。
   */
  is_default_config?: boolean;
  /**
   * 目标工作空间默认大语言模型配置标识，可为null。
   */
  llm?: null | string;
  /**
   * 工作空间名称。
   */
  name: string;
  /**
   * 工作空间默认重排模型配置标识，可为null。
   */
  rerank?: null | string;
  /**
   * 工作空间的存储类型标识。
   */
  storage_type?: null | string;
  /**
   * 工作空间所属租户UUID，不用于前端绕过租户隔离。
   */
  tenant_id: string;
  /**
   * 工作空间默认视频模型配置标识，可为null；不表示当前Wiki流程已支持视频。
   */
  video?: null | string;
  /**
   * 工作空间默认视觉模型配置标识，可为null。
   */
  vision?: null | string;
}

export interface Targetkb {
  /**
   * 知识库选用的音频转写模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  audio2text?: null | Audio2Text;
  /**
   * 知识库音频转写模型配置UUID；属于已有配置字段，不代表Wiki音频处理已完成接入。
   */
  audio2text_id?: null | string;
  /**
   * 知识库头像或图标引用；可能为null，由前端按现有头像规则展示。
   */
  avatar?: null | string;
  /**
   * 已记录的文档分块数量，不等同于Wiki页面数，也不能单独证明Wiki生成完成。
   */
  chunk_num?: number | null;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 创建该业务记录的用户UUID；历史记录可能为空。
   */
  created_by?: null | string;
  /**
   * Legacy user response fields required by Knowledge and Share responses.
   */
  created_user: CreatedUser;
  /**
   * 当前模型、知识库或工作空间的说明文本，可为null。
   */
  description?: null | string;
  /**
   * 知识库文档数量，不是Wiki页面数；Wiki页数使用/wiki/index的total_pages。
   */
  doc_num?: number | null;
  /**
   * 知识库选用的向量模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  embedding?: null | Embedding;
  /**
   * 知识库选用的embedding模型配置UUID；并非某次计算生成的向量ID。
   */
  embedding_id?: null | string;
  /**
   * 调用方关联的知识库外部标识，可为空；不等同于知识库UUID。
   */
  external_id?: null | string;
  /**
   * 知识库分享记录UUID，不等于source_kb_id或target_kb_id。
   */
  id: string;
  /**
   * 知识库选用的图片理解模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  image2text?: null | Image2Text;
  /**
   * 知识库图片理解模型配置UUID；可为空，是否可用按当前模型配置判定。
   */
  image2text_id?: null | string;
  /**
   * 知识库选用的大语言模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  llm?: null | Llm;
  /**
   * 知识库选用的大语言模型配置UUID；Wiki生成需要可用的该配置。
   */
  llm_id?: null | string;
  /**
   * 知识库名称。
   */
  name: string;
  /**
   * 父级目录或知识库节点UUID；null表示根级。
   */
  parent_id?: null | string;
  /**
   * 知识库解析配置，wiki子对象承载enabled/language/extract_prompt；历史读取可能缺少该子对象或为null，新前端按既有兼容规则处理。
   */
  parser_config?: null | ParserConfig;
  /**
   * 文档解析/切分方式标识，沿用知识库既有解析器类型。
   */
  parser_id?: null | string;
  /**
   * 知识库权限类型，沿用Private/Share/Memory等既有值；配置存在不等于Wiki页面已获得跨空间访问授权。
   */
  permission_id?: Permissionid | null;
  /**
   * 知识库选用的重排模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  reranker?: null | Reranker;
  /**
   * 知识库选用的重排模型配置UUID；可为空。
   */
  reranker_id?: null | string;
  /**
   * 知识库状态：1启用、0停用、2已删除；Wiki访问仍需满足作用域和来源条件。
   */
  status: number;
  /**
   * 知识库类型：General通用、Web网页、Third-party第三方、Folder文件夹。
   */
  type?: TypeType | null;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
  /**
   * 知识库选用的视频理解模型配置摘要，可为null；配置存在不代表相应处理流程已执行。
   */
  video2text?: null | Video2Text;
  /**
   * 知识库视频理解模型配置UUID；属于已有配置字段，不代表Wiki视频处理已完成接入。
   */
  video2text_id?: null | string;
  /**
   * 记录所属工作空间UUID；管理端访问仍按当前登录工作空间和权限校验。
   */
  workspace_id?: null | string;
  [property: string]: any;
}

// 
export interface SpaceItem {
  name: string;
  is_active: boolean;
  icon?: string;
  /**
   * Unix 毫秒时间戳。
   */
  created_at: number | null;
  /**
   * 知识库分享记录UUID，不等于source_kb_id或target_kb_id。
   */
  id: string;
  /**
   * 执行分享操作的用户UUID。
   */
  shared_by?: null | string;
  /**
   * Legacy user response fields required by Knowledge and Share responses.
   */
  shared_user: SharedUser;
  /**
   * 被分享的源知识库UUID。
   */
  source_kb_id: string;
  /**
   * 源知识库所属工作空间UUID，可为空时沿用接口既有推断规则。
   */
  source_workspace_id?: null | string;
  /**
   * 目标知识库摘要，其中parser_config.wiki为配置值；不代表跨空间复制了Wiki内容或自动扩大访问权限。
   */
  target_kb: Targetkb;
  /**
   * 目标工作空间中的知识库关联UUID，可为null；不表示Wiki页面已经复制。
   */
  target_kb_id?: null | string;
  /**
   * 目标工作空间摘要，与当前登录用户的current_workspace_id不同。
   */
  target_workspace: TargetWorkspace;
  /**
   * 此次分享的目标工作空间UUID。
   */
  target_workspace_id: string;
  /**
   * Unix 毫秒时间戳。
   */
  updated_at: number | null;
}

// 分享  to 空间
export interface ShareSpaceModalRef{
  handleOpen: (kb_id?: string, knowledgeBase?: V1KnowledgeListItem | null, spaceIds?:string) => void;
}

export interface ShareSpaceModalRefProps {
  handleShare?: () => void;
}
