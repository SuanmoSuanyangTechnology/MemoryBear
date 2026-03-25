/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:57:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-24 17:58:54
 */
/**
 * User Memory Detail Types
 * Type definitions for user memory detail views including nodes, edges, and statistics
 */

/**
 * User memory data structure
 */
export interface Data {
  id: string | number
  name: string;
  type: string;
  source: string;
  createTime: string;
  icon?: string;
  memoryInsight?: string;
  recentMemories?: {
    title: string;
    time: string;
    position: string;
    tags: string[];
  }[];
  roles?: string[];
  tags?: string[];
  username: string;
  totalNumOfMemories: number;
  footprintCity: number;
  totalNumOfPhotos: string;
  importantRelationships: number;
  aboutUs?: {
    content: string;
    [key: string]: string | number | undefined;
  };
  relationships?: {
    name: string[];
    relation: string;
    memories: number;
  }[];
  importantMoments?: {
    title: string;
    time: string;
    desc: string;
  }[];
  interestDistribution?: {
    value: number;
    name: string;
  }[];
  [key: string]: unknown;
}
/**
 * Base node properties
 */
export interface BaseProperties {
  content: string;
  created_at: number;
  associative_memory: number;
}
/**
 * Statement node properties
 */
export interface StatementNodeProperties {
  temporal_info: string;
  stmt_type: string;
  statement: string;
  valid_at: string;
  created_at: number;
  emotion_keywords: string[];
  emotion_type: string;
  emotion_subject: string;
  importance_score: number;
  associative_memory: number;
}
/**
 * Extracted entity node properties
 */
export interface ExtractedEntityNodeProperties {
  description: string;
  name: string;
  entity_type: string;
  created_at: number;
  aliases: string;
  connect_strngth: string;
  importance_score: number;
  associative_memory: number;
  community_name?: string;
}
/**
 * Memory summary node
 */
export interface MemorySummaryNode {
  id: string;
  label: 'MemorySummary';
  category: number;
  symbolSize: number;
  itemStyle: {
    color: string;
  }
  name: string;
  properties: {
    content: string;
    created_at: number;
  }
  caption: string;
  associative_memory: number;
}

/**
 * Graph node
 */
export interface Node {
  id: string;
  label: 'Dialogue' | 'ExtractedEntity' | 'Chunk' | 'MemorySummary' | 'Statement';
  category: number;
  symbolSize: number;
  name: string;
  itemStyle: {
    color: string;
  }
  properties: BaseProperties | StatementNodeProperties | ExtractedEntityNodeProperties
  caption: string;
}
/**
 * Graph edge
 */
export interface Edge {
  id: string;
  source: string;
  target: string;
  type: string;
  properties: {
    run_id: string;
    group_id: string;
    created_at: string;
    expired_at: string;
  }
  caption: string;
  value: number;
  weight: number;
}
/**
 * Graph data structure
 */
export interface GraphData {
  nodes: Node[];
  edges: Edge[];
  statistics: {
    total_nodes: number;
    total_edges: number;
    node_types: Record<string, number>;
    edge_types: Record<string, number>;
  }
}

/**
 * Node statistics item
 */
export interface NodeStatisticsItem {
  type: string;
  count: number;
  percentage: number;
}
/**
 * End user profile
 */
export interface EndUser {
  other_name: string;
  aliases: string | null;
  meta_data: Record<string, string>;
  end_user_info_id: string;
  end_user_id: string;
  created_at: string;
  updated_at: string;
}
/**
 * End user profile modal ref
 */
export interface EndUserProfileModalRef {
  handleOpen: (vo: EndUser) => void;
}
/**
 * Memory insight component ref
 */
export interface MemoryInsightRef {
  getData: () => void
}
/**
 * About me component ref
 */
export interface AboutMeRef {
  getData: () => void
}
/**
 * End user profile component ref
 */
export interface EndUserProfileRef {
  data: EndUser | null
}


/**
 * Forget engine data
 */
export interface ForgetData {
  activation_metrics: {
    total_nodes: number;
    nodes_with_activation: number;
    nodes_without_activation: number;
    average_activation_value: number;
    low_activation_nodes: number;
    timestamp: number;
    forgetting_threshold: number;
  },
  node_distribution: {
    statement_count: number;
    entity_count: number;
    summary_count: number;
    chunk_count: number;
  },
  recent_trends: {
    date: string;
    merged_count: number;
    average_activation: number;
    total_nodes: number;
    execution_time: number;
  }[],
  pending_nodes: {
    node_id: string;
    node_type: string;
    content_summary: string;
    activation_value: number;
    last_access_time: number;
  }[],
  timestamp: number;
}
/**
 * Graph detail modal ref
 */
export interface GraphDetailRef {
  handleOpen: (vo: Node) => void
}
// Community
export type CommunityNodeType = 'Community' | 'ExtractedEntity';
export type CommunityEdgeType = 'BELONGS_TO_COMMUNITY' | 'EXTRACTED_RELATIONSHIP';
export type CommunityEntityType = "Person" | "Organization" | "ORG" | "Location" | "LOC" | "Event" | "Concept" | "Time" | "Position" | "WorkRole" | "System" | "Policy" | "HistoricalPeriod" | "HistoricalState" | "HistoricalEvent" | "EconomicFactor" | "Condition" | "Numeric" | "Work";
// 社区节点
export interface CommunityTypeNode {
  id: string;
  label: 'Community';
  properties: {
    community_id: string;
    end_user_id: string;
    member_count: number;
    updated_at: string;
    name: string;
    summary: string;
    core_entities: string[];
    member_entity_ids: string[];
  };
}
// 核心实体
export interface ExtractedEntityTypeNode {
  id: string;
  label: 'ExtractedEntity';
  properties: {
    name: string;
    end_user_id: string;
    description: string;
    created_at: string;
    entity_type: CommunityEntityType;
    community_name: string;
  };
}
// 社区图谱连线
export interface CommunityEdge {
  id: string;
  target: string;
  source: string;
}
export interface CommunityStatistics {
  total_nodes: number;
  total_edges: number;
  node_types: Record<CommunityNodeType, number>;
  edge_types: Record<CommunityEdgeType, number>;
}
export interface CommunityGraphData {
  nodes: (CommunityTypeNode | ExtractedEntityTypeNode)[];
  edges: CommunityEdge[];
  statistics: CommunityStatistics;
}
