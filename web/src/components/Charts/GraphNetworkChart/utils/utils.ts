/**
 * Graph Network Utility Functions
 * 
 * Provides common utility functions for GraphNetworkChart component.
 * Includes type definitions, helper functions, and styling utilities.
 * 
 * @module graphNetworkUtils
 */

import * as d3 from 'd3';
import type { Node, EdgeClickData, EdgeType, D3Node, D3Link } from '../types'

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

/**
 * Calculate base stroke-width based on edge_type
 * - UNIDIRECTIONAL_MULTI: thickest
 * - BIDIRECTIONAL / MULTI_BIDIRECTIONAL: medium
 * - SINGLE: thinnest
 */
export const getBaseStrokeWidth = (edgeType?: EdgeType): number => {
  if (edgeType === 'UNIDIRECTIONAL_MULTI') return 2;
  if (edgeType === 'BIDIRECTIONAL' || edgeType === 'MULTI_BIDIRECTIONAL') return 1.5;
  return 0.8;
};

/**
 * Restore nodes to default state (when no selection)
 * @param nodeSel - Node selector
 * @param linkSel - Link selector
 * @param linkLabelSel - Link label selector
 * @param g - SVG group selector
 */
export const resetToDefaultState = (
  nodeSel: d3.Selection<SVGGElement, D3Node, SVGGElement, unknown>,
  linkSel: d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown>,
  linkLabelSel: d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown>,
  g: d3.Selection<SVGGElement, unknown, null, undefined>
): void => {
  // Restore node styles
  nodeSel.selectAll<SVGCircleElement, D3Node>('circle')
    .transition()
    .duration(200)
    .attr('r', d => d.symbolSize)
    .attr('fill-opacity', 1)
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);

  // Restore node outer ring styles
  nodeSel.selectAll<SVGCircleElement, D3Node>('circle.ring')
    .transition()
    .duration(200)
    .attr('r', d => d.symbolSize * 1.35)
    .attr('stroke', d => d.color)
    .attr('stroke-opacity', 0.3);

  // Restore node text styles
  nodeSel.selectAll<SVGTextElement, D3Node>('text')
    .attr('fill', '#171719')
    .attr('font-weight', 'normal');

  // Restore bidirectional edge styles
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

  // Restore unidirectional edge styles
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

  // Hide link labels
  linkLabelSel.style('display', 'none');
};

/**
 * Calculate highlighted node and link IDs
 * @param selectedNodeId - Currently selected node/link ID
 * @param selectedCategory - Currently selected category
 * @param nodes - Node list
 * @param links - Link list
 * @returns Set of highlighted node and link IDs
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
      // When link is selected, don't add any nodes to highlighted set (nodes grayed out)
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
 * Calculate highlighted nodes by category
 * @param nodes - Node list
 * @param selectedCategory - Selected category
 * @returns Set of highlighted node IDs
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
 * Calculate highlighted nodes by region ID
 * @param nodes - D3 node list
 * @param originalNodes - Original node list
 * @param regionId - Region ID
 * @returns Set of highlighted node and link IDs
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
 * Get merged relations array (a_to_b + b_to_a)
 * @param aToB - a_to_b relations array
 * @param bToA - b_to_a relations array
 * @param activeRelationIndex - Currently active relation index
 * @returns The predicate_surface of the currently active relation
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
 * Truncate node name with multi-line support
 * @param name - Node name
 * @param symbolSize - Node size
 * @param fontSize - Font size
 * @returns Truncated name (may contain newlines)
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
 * Calculate link endpoint coordinates (considering node size and margins)
 * @param source - Source node
 * @param target - Target node
 * @param offset - Offset (for bidirectional edge separation)
 * @returns Link endpoint coordinates
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
 * Calculate reverse endpoint coordinates for bidirectional edges
 * @param source - Source node
 * @param target - Target node
 * @param offset - Offset (for bidirectional edge separation)
 * @returns Link endpoint coordinates (reversed)
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

  // Reverse edge points from target to source
  const x1 = (target.x ?? 0) + (-dx / dist) * (target.symbolSize + 2) + perpX * offset;
  const y1 = (target.y ?? 0) + (-dy / dist) * (target.symbolSize + 2) + perpY * offset;
  const x2 = (source.x ?? 0) - (-dx / dist) * (source.symbolSize + 2) + perpX * offset;
  const y2 = (source.y ?? 0) - (-dy / dist) * (source.symbolSize + 2) + perpY * offset;

  return { x1, y1, x2, y2 };
};

/**
 * Calculate link label position and rotation angle
 * @param source - Source node
 * @param target - Target node
 * @param dyOffset - Y-axis offset
 * @returns Label position and rotation angle
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
 * Check if edge is unidirectional type
 * @param edgeType - Edge type
 * @returns Whether it is a unidirectional edge
 */
export const isSingleDirectional = (edgeType?: EdgeType): boolean => {
  return edgeType === 'SINGLE' || edgeType === 'UNIDIRECTIONAL_MULTI';
};

/**
 * Check if edge is bidirectional type
 * @param edgeType - Edge type
 * @returns Whether it is a bidirectional edge
 */
export const isBidirectional = (edgeType?: EdgeType): boolean => {
  return edgeType === 'BIDIRECTIONAL' || edgeType === 'MULTI_BIDIRECTIONAL';
};
