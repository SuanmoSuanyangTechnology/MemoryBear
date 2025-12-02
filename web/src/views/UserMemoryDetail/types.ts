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

export interface Node {
  id: string;
  description?: string;
  name: string;
  connect_strength?: string;
  entity_idx: number;
  entity_type?: string;
  fact_summary?: string[];
  category?: number;
  symbolSize?: number;
}
export interface Edge {
  statement: string;
  rel_id: string;
  source_id: string;
  predicate: string;
  target_id: string;
  statement_id: string;
  target?: string;
  source?: string;
}
export interface EdgeData {
  sourceNode: Node;
  edge: Edge;
  targetNode: Node;
}