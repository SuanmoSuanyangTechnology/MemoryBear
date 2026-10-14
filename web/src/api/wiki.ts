import { request } from '@/utils/request';
import type { KnowledgeBase, KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';

export type WikiPageType = 'summary' | 'entity' | 'concept';
export type WikiPageStatus = 'ready' | 'pending' | 'updating' | 'cleanup_pending' | 'failed' | 'unavailable';
export interface WikiConfig { enabled: boolean; language: string; extract_prompt: string }
export const DEFAULT_WIKI_CONFIG: WikiConfig = { enabled: false, language: 'zh-CN', extract_prompt: '' };
export const MAX_WIKI_LANGUAGE_LENGTH = 64;
export const MAX_WIKI_PROMPT_LENGTH = 12000;
export interface WikiFolder { id: string; name: string; parent_id: string | null; page_count: number }
export interface WikiPage {
  id: string;
  page_id: string;
  title: string;
  summary: string;
  type: WikiPageType;
  version: number;
  folder_id: string | null;
  folder_path: { id: string; name: string }[];
  updated_at_ms: number | null;
}
export interface WikiSource {
  ref: string;
  document_id: string;
  document_title: string;
  chunk_id: string;
  quote: string;
}
export interface WikiDetail extends WikiPage {
  content: string | null;
  status: WikiPageStatus;
  reason: string | null;
  sources: WikiSource[];
  outlinks: { page_id: string; title: string }[];
}
export interface WikiTreeNode {
  id: string;
  title: string;
  parent_id: string | null;
  node_kind: 'folder' | 'page';
  has_children?: boolean;
  page_count?: number;
  page_type?: WikiPageType;
  version?: number;
}
export interface WikiTree { parent_id: string | null; items: WikiTreeNode[]; next_cursor: string | null }
export interface WikiIndex { counts: Partial<Record<WikiPageType, number>>; total_pages: number; folders: WikiFolder[] }
export interface WikiPages { items: WikiPage[]; page: number; limit: number; total: number; has_next: boolean }
export interface WikiGraph {
  nodes: { id: string; title: string; type: WikiPageType; version: number }[];
  edges: { source: string; target: string }[];
  total_nodes: number;
  total_edges: number;
  returned_nodes: number;
  returned_edges: number;
  truncated: boolean;
  totals_scope: 'knowledge_base';
}
export interface WikiDocuments {
  items: KnowledgeBaseDocumentData[];
  page: { page: number; pagesize: number; total: number; has_next: boolean };
}

const wikiPath = (kbId: string) => `/knowledges/${encodeURIComponent(kbId)}/wiki`;
export const WIKI_PAGE_SIZE = 50;
export const WIKI_POLL_INTERVAL = 5000;
export const getWikiIndex = (kbId: string, signal?: AbortSignal) =>
  request.get<WikiIndex>(`${wikiPath(kbId)}/index`, undefined, { signal });
export const getWikiTree = (kbId: string, folderId?: string, cursor?: string, signal?: AbortSignal) =>
  request.get<WikiTree>(`${wikiPath(kbId)}/tree`, { folder_id: folderId, cursor, limit: WIKI_PAGE_SIZE }, { signal });
export const getWikiPages = (kbId: string, q: string, page: number, signal?: AbortSignal) =>
  request.get<WikiPages>(`${wikiPath(kbId)}/pages`, { q, page, limit: WIKI_PAGE_SIZE }, { signal });
export const getWikiDetail = (kbId: string, pageId: string, signal?: AbortSignal) =>
  request.get<WikiDetail>(`${wikiPath(kbId)}/pages/${encodeURIComponent(pageId)}`, undefined, { signal });
export const getWikiGraph = (kbId: string, centerId?: string, signal?: AbortSignal) =>
  request.get<WikiGraph>(`${wikiPath(kbId)}/graph`, {
    mode: centerId ? 'ego' : 'overview', center_page_id: centerId, depth: 1, limit: 500,
  }, { signal });
export const generateDocumentWiki = (kbId: string, documentId: string) =>
  request.post<{ document_id: string; task_id: string }>(`${wikiPath(kbId)}/documents/${encodeURIComponent(documentId)}/generate`);
export const getWikiDocuments = (kbId: string, page: number, q: string, signal?: AbortSignal) =>
  request.get<WikiDocuments>(`/documents/${encodeURIComponent(kbId)}/documents`, { page, pagesize: 20, keywords: q }, { signal });
export const getWikiKnowledge = (kbId: string, signal?: AbortSignal) =>
  request.get<KnowledgeBase>(`/knowledges/${encodeURIComponent(kbId)}`, undefined, { signal });

export const saveWikiConfig = (kbId: string, wiki: WikiConfig, llmId?: string) =>
  request.put(`/knowledges/${encodeURIComponent(kbId)}`, {
    parser_config: { wiki }, ...(llmId ? { llm_id: llmId } : {}),
  });
