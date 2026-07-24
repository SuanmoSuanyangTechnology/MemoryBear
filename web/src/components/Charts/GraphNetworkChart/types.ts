export interface Node {
  id: string;
  label: string;
  category: number;
  symbolSize: number;
  name: string;
  itemStyle?: {
    color: string;
  };
  caption: string;
  properties: Record<string, any>;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  vx?: number;
  vy?: number;
  [key: string]: any;
}

export interface EdgeClickData {
  id: string;
  type: string;
  predicate: string;
  predicate_surface: string;
  predicate_description?: string;
}

export type EdgeType = 'SINGLE' | 'UNIDIRECTIONAL_MULTI' | 'BIDIRECTIONAL' | 'MULTI_BIDIRECTIONAL';

export interface Edge {
  node_a: string;
  node_b: string;
  total: number;
  edge_type: EdgeType;
  a_to_b: EdgeClickData[];
  b_to_a: EdgeClickData[];
  source: string;
  target: string;
}

export interface D3Node extends d3.SimulationNodeDatum {
  id: string;
  name: string;
  category: number;
  symbolSize: number;
  color: string;
  caption: string;
}

export interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  id: string;
  source: string | D3Node;
  target: string | D3Node;
  caption?: string;
  type?: EdgeType;
  label?: string;
  total?: number;
  edge_type?: EdgeType;
  a_to_b?: EdgeClickData[];
  b_to_a?: EdgeClickData[];
}

/**
 * Transformed graph state ready for the D3 simulation
 */
export interface GraphState {
  nodes: D3Node[];
  links: D3Link[];
}

/**
 * Shared mutable refs used across the render/highlight hooks
 */
export interface GraphRefs {
  resizeObserverRef: React.MutableRefObject<ResizeObserver | null>;
  nodeSelRef: React.MutableRefObject<d3.Selection<SVGGElement, D3Node, SVGGElement, unknown> | null>;
  linkSelRef: React.MutableRefObject<d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown> | null>;
  linkLabelSelRef: React.MutableRefObject<d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown> | null>;
  gRef: React.MutableRefObject<d3.Selection<SVGGElement, unknown, null, undefined> | null>;
  graphStateRef: React.MutableRefObject<GraphState | null>;
  transformRef: React.MutableRefObject<d3.ZoomTransform | null>;
  visibleLabelIdsRef: React.MutableRefObject<Set<string>>;
}