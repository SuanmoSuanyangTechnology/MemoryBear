/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-18 10:00:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-18 10:00:00
 */
/**
 * Graph Network Utility Functions
 * 
 * Provides common utility functions for GraphNetworkChart component.
 * Includes type definitions, helper functions, and styling utilities.
 * 
 * @module graphNetworkUtils
 */

import * as d3 from 'd3';
import type { SimulationNodeDatum, SimulationLinkDatum } from 'd3';

/**
 * Default node colors
 */
export const Colors = ['#155EEF', '#02AFD5', '#FF5D34', '#6473E9', '#369F21', '#4DA8FF', '#C86AFF', '#F7BA1E', '#5B6167'];

/**
 * Region to node type mapping
 */
export const regionMapping: Record<string, string[]> = {
  prefrontal: ['Statement'],
  frontal: ['ExtractedEntity'],
  parietal: ['Perceptual'],
  occipital: ['Chunk'],
  cerebellum: ['AssistantPruned', 'AssistantOriginal'],
  brainstem: ['Dialogue', 'Conversation'],
  hippocampus: ['MemorySummary'],
  amygdala: ['Statement'],
};

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
 * 根据 edge_type 计算基础 stroke-width
 * - UNIDIRECTIONAL_MULTI 最粗
 * - BIDIRECTIONAL / MULTI_BIDIRECTIONAL 居中
 * - SINGLE 最细
 */
export const getBaseStrokeWidth = (edgeType?: EdgeType): number => {
  if (edgeType === 'UNIDIRECTIONAL_MULTI') return 2;
  if (edgeType === 'BIDIRECTIONAL' || edgeType === 'MULTI_BIDIRECTIONAL') return 1.5;
  return 0.8;
};

/**
 * 恢复节点到默认状态（无选中项时）
 * @param nodeSel - 节点选择器
 * @param linkSel - 连线选择器
 * @param linkLabelSel - 连线标签选择器
 * @param g - SVG group 选择器
 */
export const resetToDefaultState = (
  nodeSel: d3.Selection<SVGGElement, D3Node, SVGGElement, unknown>,
  linkSel: d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown>,
  linkLabelSel: d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown>,
  g: d3.Selection<SVGGElement, unknown, null, undefined>
): void => {
  // 恢复节点样式
  nodeSel.selectAll<SVGCircleElement, D3Node>('circle')
    .transition()
    .duration(200)
    .attr('r', d => d.symbolSize)
    .attr('fill-opacity', 1)
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);

  // 恢复节点外圈样式
  nodeSel.selectAll<SVGCircleElement, D3Node>('circle.ring')
    .transition()
    .duration(200)
    .attr('r', d => d.symbolSize * 1.35)
    .attr('stroke', d => d.color)
    .attr('stroke-opacity', 0.3);

  // 恢复节点文本样式
  nodeSel.selectAll<SVGTextElement, D3Node>('text')
    .attr('fill', '#171719')
    .attr('font-weight', 'normal');

  // 恢复双向边样式
  g.selectAll<SVGLineElement, D3Link>('line.bidirectional-a')
    .attr('stroke', '#A8ABB2')
    .attr('stroke-opacity', 0.4)
    .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
    .attr('marker-end', 'url(#arrow)');

  g.selectAll<SVGLineElement, D3Link>('line.bidirectional-b')
    .attr('stroke', '#A8ABB2')
    .attr('stroke-opacity', 0.4)
    .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
    .attr('marker-end', 'url(#arrow)');

  // 恢复单向边样式
  linkSel
    .attr('stroke', '#A8ABB2')
    .attr('stroke-opacity', 0.4)
    .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
    .attr('marker-end', d => {
      const edgeType = d.edge_type;
      const hasAtoB = d.a_to_b && d.a_to_b.length > 0;
      if (edgeType === 'SINGLE') {
        return hasAtoB ? 'url(#arrow)' : 'none';
      }
      return 'url(#arrow)';
    })
    .attr('marker-start', d => {
      const edgeType = d.edge_type;
      const hasBtoA = d.b_to_a && d.b_to_a.length > 0;
      if (edgeType === 'SINGLE') {
        return hasBtoA ? 'url(#arrow-source)' : 'none';
      }
      return 'none';
    })
    .attr('stroke-dasharray', 'none');

  // 隐藏连线标签
  linkLabelSel.style('display', 'none');
};

/**
 * 计算高亮的节点和连线 ID
 * @param selectedNodeId - 当前选中的节点/连线 ID
 * @param selectedCategory - 当前选中的分类
 * @param nodes - 节点列表
 * @param links - 连线列表
 * @returns 高亮的节点和连线 ID 集合
 */
export const calculateHighlightedIds = (
  selectedNodeId: string | null | undefined,
  selectedCategory: string | null | undefined,
  nodes: D3Node[],
  links: D3Link[]
): { highlightedNodeIds: Set<string>; highlightedLinkIds: Set<string> } => {
  const highlightedNodeIds = new Set<string>();
  const highlightedLinkIds = new Set<string>();

  if (selectedNodeId) {
    const isLink = links.some(link => link.id === selectedNodeId);

    if (isLink) {
      // 选中边时，不添加任何节点到高亮集合（节点置灰）
      highlightedLinkIds.add(selectedNodeId);
    } else {
      highlightedNodeIds.add(selectedNodeId);
      links.forEach(link => {
        const sourceId = typeof link.source === 'string' ? link.source : link.source.id;
        const targetId = typeof link.target === 'string' ? link.target : link.target.id;
        if (sourceId === selectedNodeId) {
          highlightedNodeIds.add(targetId);
          highlightedLinkIds.add(link.id);
        }
        if (targetId === selectedNodeId) {
          highlightedNodeIds.add(sourceId);
          highlightedLinkIds.add(link.id);
        }
      });
    }
  } else if (selectedCategory) {
    nodes.forEach(node => {
      if (node.caption === selectedCategory) {
        highlightedNodeIds.add(node.id);
      }
    });
  }

  return { highlightedNodeIds, highlightedLinkIds };
};

/**
 * 根据分类计算高亮节点
 * @param nodes - 节点列表
 * @param selectedCategory - 选中的分类
 * @returns 高亮的节点 ID 集合
 */
export const calculateHighlightedByCategory = (
  nodes: D3Node[],
  selectedCategory: string
): Set<string> => {
  const highlightedNodeIds = new Set<string>();
  nodes.forEach(node => {
    if (node.caption === selectedCategory) {
      highlightedNodeIds.add(node.id);
    }
  });
  return highlightedNodeIds;
};

/**
 * 根据区域 ID 计算高亮节点
 * @param nodes - D3 节点列表
 * @param originalNodes - 原始节点列表
 * @param regionId - 区域 ID
 * @returns 高亮的节点和连线 ID 集合
 */
export const calculateHighlightedByRegion = (
  nodes: D3Node[],
  originalNodes: Node[],
  links: D3Link[],
  regionId: string
): { highlightedNodeIds: Set<string>; highlightedLinkIds: Set<string> } => {
  const highlightedNodeIds = new Set<string>();
  const highlightedLinkIds = new Set<string>();
  const targetTypes = regionMapping[regionId] || [];

  nodes.forEach(node => {
    const originalNode = originalNodes.find(n => n.id === node.id);
    if (!originalNode) return;

    const nodeType = originalNode.caption;

    if (regionId === 'amygdala') {
      if (
        nodeType === 'Statement' &&
        originalNode.properties &&
        (originalNode.properties.emotion_type !== undefined ||
          originalNode.properties.emotion_intensity !== undefined)
      ) {
        highlightedNodeIds.add(node.id);
      }
    } else {
      if (targetTypes.includes(nodeType)) {
        highlightedNodeIds.add(node.id);
      }
    }
  });

  links.forEach(link => {
    const sourceId = typeof link.source === 'string' ? link.source : link.source.id;
    const targetId = typeof link.target === 'string' ? link.target : link.target.id;
    if (highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId)) {
      highlightedLinkIds.add(link.id as string);
    }
  });

  return { highlightedNodeIds, highlightedLinkIds };
};

/**
 * 获取合并后的关系数组（a_to_b + b_to_a）
 * @param aToB - a_to_b 关系数组
 * @param bToA - b_to_a 关系数组
 * @param activeRelationIndex - 当前激活的关系索引
 * @returns 当前激活关系的 predicate_surface
 */
export const getActiveRelationLabel = (
  aToB: EdgeClickData[] = [],
  bToA: EdgeClickData[] = [],
  activeRelationIndex: number = 0
): string => {
  const mergedRelations = [...aToB, ...bToA];
  const idx = activeRelationIndex;
  const targetRelation = mergedRelations[Math.min(idx, Math.max(mergedRelations.length - 1, 0))];
  return targetRelation?.predicate_surface || '';
};

/**
 * 截断节点名称，支持多行显示
 * @param name - 节点名称
 * @param symbolSize - 节点大小
 * @param fontSize - 字体大小
 * @returns 截断后的名称（可能包含换行）
 */
export const truncateNodeName = (
  name: string,
  symbolSize: number,
  fontSize?: number
): { lines: string[]; totalHeight: number; startY: number } => {
  const actualFontSize = fontSize || Math.max(6, Math.min(12, symbolSize * 0.25));
  const maxWidth = symbolSize * 1.2;
  const lineHeight = actualFontSize * 1.2;
  const maxLines = Math.floor((symbolSize * 1.2) / lineHeight) || 1;

  const words = name.split('');
  let line: string[] = [];
  let lines: string[] = [];
  let currentWidth = 0;
  const charWidth = actualFontSize * 0.55;

  for (let i = 0; i < words.length; i++) {
    const word = words[i];
    const wordWidth = charWidth;
    if (currentWidth + wordWidth > maxWidth && line.length > 0) {
      lines.push(line.join(''));
      line = [word];
      currentWidth = wordWidth;
    } else {
      line.push(word);
      currentWidth += wordWidth;
    }
  }
  if (line.length > 0) {
    lines.push(line.join(''));
  }

  if (lines.length > maxLines) {
    lines = lines.slice(0, maxLines);
    if (lines[maxLines - 1]) {
      lines[maxLines - 1] = lines[maxLines - 1].slice(0, -1) + '...';
    }
  }

  const totalHeight = (lines.length - 1) * lineHeight;
  const startY = -totalHeight / 2;

  return { lines, totalHeight, startY };
};

/**
 * 计算连线端点坐标（考虑节点大小和边距）
 * @param source - 源节点
 * @param target - 目标节点
 * @param offset - 偏移量（用于双向边分离）
 * @returns 连线端点坐标
 */
export const calculateLinkEndpoints = (
  source: D3Node,
  target: D3Node,
  offset: number = 0
): { x1: number; y1: number; x2: number; y2: number } => {
  const dx = (target.x ?? 0) - (source.x ?? 0);
  const dy = (target.y ?? 0) - (source.y ?? 0);
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;
  const perpX = -dy / dist;
  const perpY = dx / dist;

  const x1 = (source.x ?? 0) + (dx / dist) * (source.symbolSize + 2) + perpX * offset;
  const y1 = (source.y ?? 0) + (dy / dist) * (source.symbolSize + 2) + perpY * offset;
  const x2 = (target.x ?? 0) - (dx / dist) * (target.symbolSize + 2) + perpX * offset;
  const y2 = (target.y ?? 0) - (dy / dist) * (target.symbolSize + 2) + perpY * offset;

  return { x1, y1, x2, y2 };
};

/**
 * 计算双向边的反向端点坐标
 * @param source - 源节点
 * @param target - 目标节点
 * @param offset - 偏移量（用于双向边分离）
 * @returns 连线端点坐标（反向）
 */
export const calculateBidirectionalReverseEndpoints = (
  source: D3Node,
  target: D3Node,
  offset: number = 0
): { x1: number; y1: number; x2: number; y2: number } => {
  const dx = (target.x ?? 0) - (source.x ?? 0);
  const dy = (target.y ?? 0) - (source.y ?? 0);
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;
  const perpX = dy / dist;
  const perpY = -dx / dist;

  // 反向边从 target 指向 source
  const x1 = (target.x ?? 0) + (-dx / dist) * (target.symbolSize + 2) + perpX * offset;
  const y1 = (target.y ?? 0) + (-dy / dist) * (target.symbolSize + 2) + perpY * offset;
  const x2 = (source.x ?? 0) - (-dx / dist) * (source.symbolSize + 2) + perpX * offset;
  const y2 = (source.y ?? 0) - (-dy / dist) * (source.symbolSize + 2) + perpY * offset;

  return { x1, y1, x2, y2 };
};

/**
 * 计算连线标签位置和旋转角度
 * @param source - 源节点
 * @param target - 目标节点
 * @param dyOffset - Y 轴偏移量
 * @returns 标签位置和旋转角度
 */
export const calculateLinkLabelPosition = (
  source: D3Node,
  target: D3Node,
  dyOffset: number = -8
): { x: number; y: number; rotation: number } => {
  const x = ((source.x ?? 0) + (target.x ?? 0)) / 2;
  const y = ((source.y ?? 0) + (target.y ?? 0)) / 2 + dyOffset;

  const dx = (target.x ?? 0) - (source.x ?? 0);
  const dy = (target.y ?? 0) - (source.y ?? 0);
  const angle = Math.atan2(dy, dx) * 180 / Math.PI;
  const rotation = angle > 90 || angle < -90 ? angle + 180 : angle;

  return { x, y, rotation };
};

/**
 * 判断边是否为单向边类型
 * @param edgeType - 边类型
 * @returns 是否为单向边
 */
export const isSingleDirectional = (edgeType?: EdgeType): boolean => {
  return edgeType === 'SINGLE' || edgeType === 'UNIDIRECTIONAL_MULTI';
};

/**
 * 判断边是否为双向边类型
 * @param edgeType - 边类型
 * @returns 是否为双向边
 */
export const isBidirectional = (edgeType?: EdgeType): boolean => {
  return edgeType === 'BIDIRECTIONAL' || edgeType === 'MULTI_BIDIRECTIONAL';
};
