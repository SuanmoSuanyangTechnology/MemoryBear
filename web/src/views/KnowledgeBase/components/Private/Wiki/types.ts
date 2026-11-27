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
// Wiki directory tree (paginated level by level)
export type PageType = 'summary' | 'entity' | 'concept';

// Supports a single type or a comma-separated combination of up to three types.
export type PageTypesQuery = PageType | `${PageType},${PageType}` | `${PageType},${PageType},${PageType}`;
export interface  WikiTreeQuery {
  /**
   * Parent directory
   */
  folder_id?: string;
  /**
   * Comma-separated: summary, entity, concept;
   * omit to mean all.
   * The Wiki tab passes entity,concept;
   * the summary tab passes summary.
   */
  page_types?: PageTypesQuery;
  /**
   * Page number, starting from 1;
   * the directory tree is paginated independently by the current folder_id and page_types.
   */
  page?: number;
  /**
   * Page size, default 50, up to 200;
   * the directory tree counts direct folder nodes and page nodes together.
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

  // Present when node_kind = folder
  has_children?: boolean;
  page_count?: number;
}
export interface WikiTreeResponse {
  parent_id: string;
  items: WikiTreeItem[];
  page: PageResponse
}

// Wiki index and statistics
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

// Wiki page list and search
export interface WikiPagesQuery {
  /**
   * Case-insensitive contains search on title or summary;
   * defaults to an empty string, does not search the body.
   */
  keyword?: string;
  /**
   * Comma-separated: summary, entity, concept;
   * omit to mean all.
   * The Wiki tab passes entity,concept;
   * the summary tab passes summary.
   */
  page_types?: PageTypesQuery;
  /**
   * Page number, starting from 1.
   */
  page?: number;
  /**
   * Page size.
   * Default 50, up to 200.
   */
  pagesize?: number;
  /** Filter by source document, used by the file list "Related Wiki" drawer */
  document_id?: string;
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

// Wiki page detail and sources
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

// Wiki page relation graph
export interface WikiGraphQuery {
  /**
   * overview=global overview; ego=neighborhood of the center page.
   */
  mode: 'overview' | 'ego';
  /**
   * Required in ego mode; may be omitted in overview mode.
   */
  center_page_id?: string;
  /**
   * Comma-separated summary/entity/concept;
   * note that the graph parameter is named types, not page_types.
   */
  types?: PageTypesQuery;
  /**
   * ego expansion depth, default 1, max 2.
   */
  depth?: number;
  /**
   * Requested node count upper limit;
   * in practice it is also capped by the server-side node budget, and edges have a separate budget.
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
