import { useEffect, useRef } from 'react';
import { Graph } from '@antv/x6';
import { register } from '@antv/x6-react-shape';
import { graphNodeLibrary } from '../constant/graphNodeLibrary';
import { nodeRegisterLibrary } from '../constant/nodeShapes';
import { edgeAttrs } from '../constant/edges';
import { nodeLibrary } from '../constant/nodeLibrary';
import { leftPortGroup } from '../constant/ports';

const previewRightPortGroup = {
  ...leftPortGroup,
  attrs: {
    body: {
      ...leftPortGroup.attrs.body,
      x: 0.75,
    },
  },
};

export interface WorkflowPreviewNode {
  id: string;
  name: string;
  type: string;
  x: number;
  y: number;
  config?: Record<string, unknown>;
  parentId?: string;
  branches?: { label: string; name: string }[];
}

export interface WorkflowPreviewEdge {
  source: string;
  target: string;
  label?: string;
  type?: string;
}

nodeRegisterLibrary.forEach(shape => {
  register({
    ...shape,
    shape: `workflow-preview-${shape.shape}`,
  });
});

/** Owns a read-only X6 graph without editor state, requests or mutation handlers. */
export const useWorkflowPreview = (nodes: WorkflowPreviewNode[], edges: WorkflowPreviewEdge[]) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const graph = new Graph({
      container: containerRef.current,
      autoResize: true,
      grid: { visible: true, type: 'dot', size: 10 },
      panning: true,
      mousewheel: { enabled: true, modifiers: null, minScale: 0.2, maxScale: 2 },
      interacting: false,
    });
    graphRef.current = graph;
    nodes.forEach(node => {
      const layout = graphNodeLibrary[node.type] ?? graphNodeLibrary.default;
      const definition = nodeLibrary.flatMap(group => group.nodes).find(item => item.type === node.type);
      const config = Object.fromEntries(
        Object.entries(definition?.config ?? {}).map(([key, field]) => [key, {
          ...field,
          defaultValue: node.config?.[key] ?? field.defaultValue,
        }]),
      );
      const outputPortX = ['loop', 'iteration'].includes(node.type) ? '100%' : layout.width;
      graph.addNode({
        ...layout,
        id: node.id,
        shape: `workflow-preview-${layout.shape}`,
        x: node.x,
        y: node.y,
        height: node.branches?.length ? 64 + node.branches.length * 26 : layout.height,
        ports: {
          ...layout.ports,
          groups: {
            ...layout.ports?.groups,
            right: previewRightPortGroup,
          },
          items: [
            { id: 'input', group: 'left', args: { x: 0, y: 26 } },
            ...(node.branches?.length
              ? node.branches.map((branch, index) => ({
                id: branch.label,
                group: 'right',
                args: { x: outputPortX, y: 65 + index * 26 },
              }))
              : [{ id: 'output', group: 'right', args: { x: outputPortX, y: 26 } }]),
          ].filter(port => layout.ports?.groups?.[port.group]),
        },
        data: {
          previewBranches: node.branches,
          name: node.name,
          type: node.type,
          icon: definition?.icon,
          config,
          readOnly: true,
        },
      });
    });
    const previewEdges = [...edges];
    nodes.filter(node => ['loop', 'iteration'].includes(node.type)).forEach(parent => {
      const children = nodes.filter(node => node.parentId === parent.id);
      if (children.some(node => ['cycle-start', 'start'].includes(node.type))) return;

      let startId = `${parent.id}-preview-start`;
      while (graph.getCellById(startId)) startId += '-start';
      const layout = graphNodeLibrary['cycle-start'];
      const start = graph.addNode({
        ...layout,
        id: startId,
        shape: `workflow-preview-${layout.shape}`,
        x: children.length ? Math.min(...children.map(node => node.x)) - 84 : parent.x + 24,
        y: children.length ? Math.min(...children.map(node => node.y)) + 8 : parent.y + 70,
        ports: {
          ...layout.ports,
          groups: {
            ...layout.ports?.groups,
            right: previewRightPortGroup,
          },
          items: [{ id: 'output', group: 'right', args: { x: 36, y: 18 } }],
        },
        data: { type: 'cycle-start', parentId: parent.id, readOnly: true },
      });
      const container = graph.getCellById(parent.id);
      if (container?.isNode()) container.addChild(start);

      const childIds = new Set(children.map(node => node.id));
      const explicitEntries = edges.filter(edge => edge.source === parent.id && childIds.has(edge.target));
      if (explicitEntries.length) {
        previewEdges.forEach((edge, index) => {
          if (edge.source === parent.id && childIds.has(edge.target)) {
            previewEdges[index] = { ...edge, source: startId };
          }
        });
      } else {
        children.filter(child => !edges.some(edge => childIds.has(edge.source) && edge.target === child.id))
          .forEach(child => previewEdges.push({ source: startId, target: child.id }));
      }
    });
    const resizeGroup = (id: string, visited = new Set<string>()) => {
      if (visited.has(id)) return;
      visited.add(id);
      const parent = graph.getCellById(id);
      if (!parent?.isNode()) return;
      nodes.filter(node => node.parentId === id).forEach(child => {
        resizeGroup(child.id, visited);
        const cell = graph.getCellById(child.id);
        if (cell?.isNode() && !cell.isAncestorOf(parent)) parent.addChild(cell);
      });
      const children = parent.getChildren()?.filter(cell => cell.isNode()) ?? [];
      if (!children.length) return;
      const boxes = children.map(cell => cell.getBBox());
      const box = parent.getBBox();
      const x = Math.min(box.x, ...boxes.map(item => item.x - 20));
      const y = Math.min(box.y, ...boxes.map(item => item.y - 64));
      parent.position(x, y);
      parent.resize(
        Math.max(box.right, ...boxes.map(item => item.right + 20)) - x,
        Math.max(box.bottom, ...boxes.map(item => item.bottom + 20)) - y,
      );
      parent.setZIndex(-1);
    };
    nodes.filter(node => ['loop', 'iteration'].includes(node.type)).forEach(node => resizeGroup(node.id));
    previewEdges.forEach(edge => {
      graph.addEdge({
        ...edgeAttrs,
        source: { cell: edge.source, port: edge.label && graph.getCellById(edge.source)?.isNode()
          && graph.getNodes().find(node => node.id === edge.source)?.hasPort(edge.label) ? edge.label : 'output' },
        target: { cell: edge.target, port: 'input' },
        labels: [],
        connector: { name: 'smooth' },
      });
    });
    const fit = () => graph.zoomToFit({ padding: 24, maxScale: 1 });
    const frame = requestAnimationFrame(fit);
    const observer = new ResizeObserver(fit);
    observer.observe(containerRef.current);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      graphRef.current = null;
      graph.dispose();
    };
  }, [nodes, edges]);

  return {
    containerRef,
    zoomIn: () => graphRef.current?.zoom(0.2),
    zoomOut: () => graphRef.current?.zoom(-0.2),
    fit: () => graphRef.current?.zoomToFit({ padding: 24, maxScale: 1 }),
  };
};
