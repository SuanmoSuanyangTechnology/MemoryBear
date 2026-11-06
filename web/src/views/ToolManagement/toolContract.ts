import type { ToolItem, ToolType } from './types';

export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default?: unknown;
  enum?: unknown[] | null;
  minimum?: number | null;
  maximum?: number | null;
  pattern?: string | null;
}

export interface ToolMethod {
  method_id: string;
  operation_key: string | null;
  name: string;
  description: string;
  parameters: ToolParameter[];
  method?: string | null;
  path?: string | null;
}

export interface ExternalRef {
  source: 'community_market';
  resource_id: string;
  version: string;
  status: 'published' | 'unlisted' | 'unknown' | null;
}

export interface ToolDetail extends ToolItem {
  builtin_config: Record<string, unknown> | null;
  custom_config: Record<string, unknown> | null;
  mcp_config: Record<string, unknown> | null;
  workflow_config: Record<string, unknown> | null;
  updated_at: number;
}

export interface ToolUpdate {
  name?: string;
  description?: string;
  icon?: string | null;
  config?: object;
  is_enabled?: boolean;
  tags?: string[];
}

export interface ToolMethodsBatch {
  items: {
    tool_id: string;
    name: string;
    tool_type: ToolType;
    origin: 'local' | 'external';
    is_active: boolean;
    method_count: number;
    methods: ToolMethod[];
  }[];
  missing_tool_ids: string[];
}
export enum Status {
    Available = "available",
    ConfiguredDisabled = "configured_disabled",
    Error = "error",
    Unconfigured = "unconfigured",
}
export interface StatusCount {
    count:  number;
    status: Status;
}
export interface ToolStatistics {
    /**
     * is_active=false 的数量
     */
    disabled_count: number;
    /**
     * is_active=true 的数量
     */
    enabled_count: number;
    /**
     * 按状态统计；仅包含实际存在的状态
     */
    status_counts: StatusCount[];
    /**
     * 工具总数
     */
    total_tools: number;
    /**
     * 按工具类型统计，键为 builtin / custom / mcp / workflow
     */
    type_counts: Record<ToolType, number>;
}
