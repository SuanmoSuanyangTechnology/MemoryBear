// 知识库表单数据类型
export interface KnowledgeBaseFormData {
  workspace_id?: string; // 工作空间ID
  id?: string; // 知识库ID 新建时为空
  name?: string; // 知识库名称
  description?: string; // 描述
  avatar?: string; // 头像
  embedding_id?: string; // 嵌入模型ID
  llm_id?: string; // LLM模型ID
  image2text_id?: string; // 图片转文本模型ID
  reranker_id?: string; // 重排模型ID
  chat_id?: string; // 聊天模型ID
  permission_id?: string; // 权限ID
  parent_id?: string; // 父ID
  type?: string; // 知识库类型
  status?: number; // 状态
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
  permission_id?: string;
  type: string;
  status?: number; // 状态 1 启用 0 禁用
  descriptionItems?: Record<string, unknown>[];
}

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
}

export interface RecallTestData {
  page_content: string;
  vector: null | number[];
  metadata: RecallTestMetadata;
  children: null | RecallTestData[];
}

export interface RecallTestParams {
  query?: string; // 查询问题
  kb_ids?: string[]; // 知识库ID
  similarity_threshold?: number; // 相似度阈值
  vector_similarity_weight?: number; //语义相似度权重
  top_k?: number;
  hybrid?: boolean; // 是否混合检索
  hybrid_weight?: string;
}
// 文件夹 
export interface FolderFormData {
  id?: string; // 文件夹ID 新建时为空
  kb_id: string; // 知识库ID
  parent_id: string; // 父ID 最顶层=知识库id
  folder_name?: string; // 文件夹名称
  page?: number;
  pagesize?: number;
  // description: string; // 描述
  createdAt?: string;
  updatedAt?: string;
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
  handleOpen: (knowledgeBaseListItem?: KnowledgeBaseListItem | null, type?: string) => void;
}
export interface CreateModalRefProps {
  refreshTable?: () => void;
}
//
export interface RecallTestDrawerRef {
  handleOpen: (knowledgeBaseId?: string) => void;
}

export interface CreateFolderModalRef {
  handleOpen: (folder?: FolderFormData | null,type?:string) => void;
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
  handleOpen: (kb_id?: string,knowledgeBase?: KnowledgeBase | null) => void;
}

export interface ShareModalRefProps {
  handleShare?: (selectedData: { checkedItems: any[], selectedItem: any | null }) => void;
}

// 创建数据集
export interface CreateDatasetModalRef {
  handleOpen: (kb_id?: string,parent_id?: string) => void;
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
  [key: string]: unknown;
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
  api_keys: ModelAPIKey[];
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
  id: string;
  username: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
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
}

// 
export interface SpaceItem {
  id: string; // 空间ID
  name: string; // 空间名称
  icon?: string | null; // 空间图标
  iconType?: string | null; // 空间图标类型
  tenant_id: string; // 租户ID
  description?: string | null; // 描述
  created_at?: number; // 创建时间（时间戳）
  updated_at?: string; // 更新时间
  is_active: boolean; // 是否启用
}

// 分享空item
export interface ShareSpaceItem{

}
// 分享  to 空间
export interface ShareSpaceModalRef{
  handleOpen: (kb_id?: string,knowledgeBase?: KnowledgeBase | null, spaceIds?:string) => void;
}

export interface ShareSpaceModalRefProps {
  handleShare?: () => void;
}
