import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import * as d3 from 'd3';

import type { WikiPage, WikiRelation } from './types';

interface GraphNode extends d3.SimulationNodeDatum {
  id: string;
  page: WikiPage;
  radius: number;
  color: string;
}

interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
  weight: number;
}

interface WikiD3GraphProps {
  pages: WikiPage[];
  relations: WikiRelation[];
  colors: Record<string, string>;
  selectedPageId?: string;
  onSelect: (id: string) => void;
}

export interface WikiD3GraphRef {
  zoomIn: () => void;
  zoomOut: () => void;
  fitView: () => void;
}

const WikiD3Graph = forwardRef<WikiD3GraphRef, WikiD3GraphProps>(({ pages, relations, colors, selectedPageId, onSelect }, ref) => {
  const controlsRef = useRef<WikiD3GraphRef | null>(null);

  useImperativeHandle(ref, () => ({
    zoomIn: () => controlsRef.current?.zoomIn(),
    zoomOut: () => controlsRef.current?.zoomOut(),
    fitView: () => controlsRef.current?.fitView(),
  }), []);

  const containerRef = useRef<HTMLDivElement>(null);
  const selectionRef = useRef(selectedPageId);
  const onSelectRef = useRef(onSelect);
  const highlightRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    selectionRef.current = selectedPageId;
    highlightRef.current?.();
  }, [selectedPageId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const nodes: GraphNode[] = pages.map((page) => ({
      id: page.id,
      page,
      radius: Math.max(12, Math.min(34, 14 + page.relatedIds.length * 2 + page.importance * 24)) / 2,
      color: colors[page.category] ?? '#6B8FA3',
    }));
    const ids = new Set(nodes.map((node) => node.id));
    const seenPairs = new Set<string>();
    const links: GraphLink[] = relations
      .filter(({ source, target }) => {
        if (!ids.has(source) || !ids.has(target)) {
          return false;
        }

        const key = JSON.stringify([source, target].sort());
        if (seenPairs.has(key)) {
          return false;
        }

        seenPairs.add(key);
        return true;
      })
      .map((relation) => ({
        source: relation.source,
        target: relation.target,
        weight: relation.weight,
      }));
    const endpointId = (endpoint: GraphLink['source']) =>
      typeof endpoint === 'object' ? endpoint.id : String(endpoint);
    let hoveredId: string | undefined;
    const svg = d3.select(container).append('svg')
      .attr('width', '100%')
      .attr('height', '100%')
      .style('display', 'block');
    const group = svg.append('g');
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 5])
      .on('zoom', (event) => {
        group.attr('transform', event.transform.toString());
      });
    svg.call(zoom).on('dblclick.zoom', null);
    controlsRef.current = {
      zoomIn: () => svg.call(zoom.scaleBy, 1.2),
      zoomOut: () => svg.call(zoom.scaleBy, 1 / 1.2),
      fitView: () => {
        const bounds = group.node()?.getBBox();
        const { width, height } = container.getBoundingClientRect();
        if (!bounds || !width || !height || !bounds.width || !bounds.height) return;

        const scale = Math.max(0.2, Math.min(5,
          Math.max(1, width - 80) / bounds.width,
          Math.max(1, height - 120) / bounds.height,
        ));
        const transform = d3.zoomIdentity
          .translate(width / 2, height / 2)
          .scale(scale)
          .translate(-bounds.x - bounds.width / 2, -bounds.y - bounds.height / 2);
        svg.call(zoom.transform, transform);
      },
    };
    const edges = group.append('g').selectAll('line').data(links).join('line')
      .attr('stroke', '#A8ABB2');
    const nodeGroups = group.append('g').selectAll<SVGGElement, GraphNode>('g').data(nodes).join('g')
      .style('cursor', 'pointer');
    const circles = nodeGroups.append('circle').attr('fill', (node) => node.color);
    const rings = nodeGroups.append('circle').attr('fill', 'none').attr('class', 'ring');
    const labels = nodeGroups.append('text')
      .text((node) => node.page.title)
      .attr('text-anchor', 'middle')
      .attr('dy', (node) => node.radius + 16)
      .attr('font-size', 11)
      .style('pointer-events', 'none');
    nodeGroups.append('title').text((node) => `${node.page.title}\n${node.page.summary}`);

    const highlight = () => {
      const activeId = hoveredId ?? selectionRef.current;
      const active = !!activeId && ids.has(activeId);
      const neighbors = new Set<string>(activeId ? [activeId] : []);
      const connected = (link: GraphLink) =>
        endpointId(link.source) === activeId || endpointId(link.target) === activeId;
      links.forEach((link) => {
        if (connected(link)) {
          neighbors.add(endpointId(link.source));
          neighbors.add(endpointId(link.target));
        }
      });
      circles
        .attr('r', (node) => node.radius)
        .attr('fill-opacity', (node) => !active || neighbors.has(node.id) ? 1 : 0.15)
        .attr('stroke', (node) => !active || neighbors.has(node.id) ? '#fff' : '#ccc')
        .attr('stroke-width', (node) => !active || neighbors.has(node.id) ? 1.5 : 0.5);
      rings
        .attr('r', (node) => node.radius * 1.35)
        .attr('stroke', (node) => node.color)
        .attr('stroke-width', 1)
        .attr('stroke-opacity', (node) => node.id === selectionRef.current ? 0.3 : 0);
      labels.attr('fill', (node) => !active || neighbors.has(node.id) ? '#171719' : '#bbb');
      edges
        .attr('opacity', (link) => !active ? 0.4 : connected(link) ? 0.6 : 0.15)
        .attr('stroke-width', (link) => !active ? 0.8 : connected(link) ? 1.5 : 0.48);
    };
    highlightRef.current = highlight;
    nodeGroups
      .on('mouseenter', (_event, node) => {
        hoveredId = node.id;
        highlight();
      })
      .on('mouseleave', () => {
        hoveredId = undefined;
        highlight();
      })
      .on('click', (event, node) => {
        event.stopPropagation();
        selectionRef.current = node.id;
        onSelectRef.current(node.id);
        highlight();
      });

    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink<GraphNode, GraphLink>(links).id((node) => node.id).distance(110))
      .force('charge', d3.forceManyBody().strength(-240))
      .force('collision', d3.forceCollide<GraphNode>().radius((node) => node.radius + 12))
      .on('tick', () => {
        edges.each(function (link) {
          const source = link.source as GraphNode;
          const target = link.target as GraphNode;
          const dx = (target.x ?? 0) - (source.x ?? 0);
          const dy = (target.y ?? 0) - (source.y ?? 0);
          const distance = Math.hypot(dx, dy) || 1;
          const ux = dx / distance;
          const uy = dy / distance;
          const sourcePadding = source.radius + 2;
          const targetPadding = target.radius + 2;
          d3.select(this)
            .attr('x1', (source.x ?? 0) + ux * sourcePadding)
            .attr('y1', (source.y ?? 0) + uy * sourcePadding)
            .attr('x2', (target.x ?? 0) - ux * targetPadding)
            .attr('y2', (target.y ?? 0) - uy * targetPadding);
        });
        nodeGroups.attr('transform', (node) => `translate(${node.x ?? 0},${node.y ?? 0})`);
      });
    nodeGroups.call(d3.drag<SVGGElement, GraphNode>()
      .on('start', (event, node) => {
        if (!event.active) {
          simulation.alphaTarget(0.3).restart();
        }
        node.fx = node.x;
        node.fy = node.y;
      })
      .on('drag', (event, node) => {
        node.fx = event.x;
        node.fy = event.y;
      })
      .on('end', (event, node) => {
        if (!event.active) {
          simulation.alphaTarget(0);
        }
        node.fx = null;
        node.fy = null;
      }));
    const resize = () => {
      const { width, height } = container.getBoundingClientRect();
      simulation.force('center', d3.forceCenter(width / 2, height / 2));
      simulation.alpha(0.3).restart();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    highlight();

    return () => {
      observer.disconnect();
      simulation.stop();
      highlightRef.current = null;
      controlsRef.current = null;
      svg.remove();
    };
  }, [pages, relations, colors]);

  return <div ref={containerRef} className="rb:absolute rb:inset-0" />;
});

WikiD3Graph.displayName = 'WikiD3Graph';

export default WikiD3Graph;
