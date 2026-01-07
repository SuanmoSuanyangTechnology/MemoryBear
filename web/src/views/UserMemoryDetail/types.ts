import type { Dayjs } from "dayjs";

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
export interface BaseProperties {
  content: string;
  created_at: number;
}
export interface StatementNodeProperties {
  temporal_info: string;
  stmt_type: string;
  statement: string;
  valid_at: string;
  created_at: number;
}
export interface ExtractedEntityNodeProperties {
  description: string;
  name: string;
  entity_type: string;
  created_at: number;
}
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

}

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

export interface NodeStatisticsItem {
  type: string;
  count: number;
  percentage: number;
}
export interface EndUser {
  end_user_id: string;
  id: string;
  other_name: string;
  position: string;
  department: string;
  contact: string;
  phone: string;
  hire_date: string | number | Dayjs | null;
  updatetime_profile?: number;
}
export interface EndUserProfileModalRef {
  handleOpen: (vo: EndUser) => void;
}
export interface MemoryInsightRef {
  getData: () => void
}
export interface AboutMeRef {
  getData: () => void
}
export interface EndUserProfileRef {
  data: EndUser | null
}


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