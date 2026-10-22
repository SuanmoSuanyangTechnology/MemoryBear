export const WIKI_CATALOG_PAGE_ID = '__wiki_catalog__';
export const WIKI_SUMMARY_PAGE_ID = '__wiki_summary__';

export type WikiWorkspaceMode = 'files' | 'wiki' | 'summary' | 'wikiGraph' | 'faq' | 'knowledgeGraph';

export interface WikiPage {
  id: string;
  title: string;
  category: string;
  folderId?: string;
  folderPath?: FolderPathItem[];
  summary: string;
  markdown: string;
  sourceIds: string[];
  relatedIds: string[];
  importance: number;
}

export interface WikiRelation {
  source: string;
  target: string;
  description: string;
  weight: number;
}

export interface WikiData {
  pages: WikiPage[];
  relations: WikiRelation[];
}

export interface RawKnowledgeGraphNode {
  id?: unknown;
  entity_name?: unknown;
  entity_type?: unknown;
  description?: unknown;
  pagerank?: unknown;
  source_id?: unknown;
}

export interface RawKnowledgeGraphEdge {
  src_id?: unknown;
  tgt_id?: unknown;
  description?: unknown;
  weight?: unknown;
}


export interface PageResponse {
  page: number;
  pagesize: number;
  total: number;
  hasnext: boolean;
}
// Wiki 目录树（逐层分页）
export type PageType = 'summary' | 'entity' | 'concept';

// 支持单个类型或最多三个类型的逗号分隔组合。
export type PageTypesQuery = PageType | `${PageType},${PageType}` | `${PageType},${PageType},${PageType}`;
export interface  WikiTreeQuery {
  /**
   * 父级目录
   */
  folder_id?: string;
  /**
   * 逗号分隔：summary、entity、concept；
   * 省略表示全部。
   * Wiki 页签传 entity,concept
   * 摘要页签传 summary。
   */
  page_types?: PageTypesQuery;
  /**
   * 页码，从 1 开始；
   * 目录树按当前 folder_id 和 page_types 独立分页。
   */
  page?: number;
  /**
   * 每页条数，默认 50，上限 200；
   * 目录树按直属目录节点和页面节点合计。
   */
  pagesize?: number;
}
export interface WikiTreeItem {
  id: string;
  node_kind: 'page' | 'folder';
  parent_id: string;
  title: string;
  page_type: PageType
  version: number;

  // node_kind = folder 时，存在下面字段
  has_children?: boolean;
  page_count?: number;
}
export interface WikiTreeResponse {
  parent_id: string;
  items: WikiTreeItem[];
  page: PageResponse
}

// Wiki 索引与统计
export interface WikiIndexFolder {
  id: string;
  name: string;
  parent_id: string;
  page_count: number;
}
export interface WikiIndexResponse {
  counts: {
    summary: number;
    entity: number;
    concept: number;
  },
  total_pages: number;
  folders: WikiIndexFolder[]
}

// Wiki 页面列表与搜索
export interface WikiPagesQuery {
  /**
   * 按标题或简述执行不区分大小写的包含搜索；
   * 默认空字符串，不搜索正文。
   */
  keyword?: string;
  /**
   * 逗号分隔：summary、entity、concept；
   * 省略表示全部。
   * Wiki 页签传 entity,concept
   * 摘要页签传 summary。
   */
  page_types?: PageTypesQuery;
  /**
   * 页码，从 1 开始。
   */
  page?: number;
  /**
   * 每页页面条数
   * 默认 50，上限 200。
   */
  pagesize?: number;
}
export interface FolderPathItem {
  id: string;
  name: string;
}
export interface WikiPageItem {
  id: string;
  page_id: string;
  title: string;
  summary: string;
  type: PageType
  version: number;
  folder_id: string;
  folder_path: FolderPathItem[],
  updated_at_ms: number;
}
export interface WikiPagesResponse {
  items: WikiPageItem[];
  page: PageResponse;
}

// Wiki 页面详情与来源
export interface SourceItem {
  ref: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  quote: string;
}
export interface OutlinkItem {
  page_id: string;
  title: string;
}
export interface WikiPageDetail {
    id: string;
    page_id: string;
    title: string;
    summary: string;
    type: PageType;
    version: number;
    folder_id: string;
    folder_path: FolderPathItem[]
    updated_at_ms: number;
    status: string;
    content: string | null,
    sources: SourceItem[]
    outlinks: OutlinkItem[];
    reason: string;
}

// Wiki 页面关系图
export interface WikiGraphQuery {
  /**
   * overview=全局概览；ego=中心页面邻域。
   */
  mode: 'overview' | 'ego';
  /**
   * ego 模式必填；overview 模式可省略。
   */
  center_page_id?: string;
  /**
   * 逗号分隔 summary/entity/concept；
   * 注意图谱参数名为 types，不是 page_types。
   */
  types?: PageTypesQuery;
  /**
   * ego 扩展深度，默认 1，最多 2。
   */
  depth?: number;
  /**
   * 请求节点数上限；
   * 实际还受服务端节点预算限制，边另有独立预算。
   */
  limit?: number;
}
export interface NodeItem {
  id: string;
  title: string;
  type: PageType;
  version: number;
}
export interface WikiGraph {
  nodes: NodeItem[],
  edges: {
      source: string;
      target: string;
  }[]
  total_nodes: number;
  total_edges: number;
  returned_nodes: number;
  returned_edges: number;
  truncated: boolean;
  totals_scope: string;
}