import { useEffect, useRef } from 'react';
import { Graph } from '@antv/x6';
import { register } from '@antv/x6-react-shape';
import { useTranslation } from 'react-i18next';
import ClusterNode from './Nodes/ClusterNode';
import { edgeAttrs } from '@/views/Workflow/constant/edges';
import { leftPortGroup } from '@/views/Workflow/constant/ports';

export interface ClusterAgent {
  name: string;
  role: string;
}

const CLUSTER_NODE_WIDTH = 240;
const CLUSTER_NODE_HEIGHT = 76;

register({
  shape: 'cluster-node',
  width: CLUSTER_NODE_WIDTH,
  height: CLUSTER_NODE_HEIGHT,
  component: ClusterNode,
});

const iconMap = {
  start: 'rb:bg-[url("@/assets/images/workflow/start.svg")]',
  agent: 'rb:bg-[url("@/assets/images/workflow/agent.svg")]',
  end: 'rb:bg-[url("@/assets/images/workflow/end.svg")]',
};

const previewRightPortGroup = {
  ...leftPortGroup,
  attrs: {
    body: {
      ...leftPortGroup.attrs.body,
      x: 0.75,
    },
  },
};

/** Manages the read-only membership diagram and releases canvas resources. */
export const useClusterPreview = (agents: ClusterAgent[], title: string, serial = false, modeLabel?: string) => {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);

  useEffect(() => {
    if (!containerRef.current || !agents.length) return;
    const graph = new Graph({
      container: containerRef.current,
      autoResize: true,
      grid: { visible: true, type: 'dot', size: 10 },
      panning: true,
      mousewheel: { enabled: true, modifiers: null, minScale: 0.2, maxScale: 2 },
      interacting: false,
    });
    graphRef.current = graph;

    const gapX = 320;
    const gapY = 140;

    if (serial) {
      const addNode = (id: string, label: string, desc: string, x: number, y: number, iconType: 'start' | 'agent' | 'end' = 'agent') => {
        graph.addNode({
          id,
          shape: 'cluster-node',
          x,
          y,
          width: CLUSTER_NODE_WIDTH,
          height: CLUSTER_NODE_HEIGHT,
          ports: {
            groups: {
              left: leftPortGroup,
              right: previewRightPortGroup,
            },
            items: [
              { id: 'input', group: 'left', args: { x: 0, y: 26 } },
              { id: 'output', group: 'right', args: { x: CLUSTER_NODE_WIDTH, y: 26 } },
            ],
          },
          data: {
            name: label,
            description: desc,
            icon: iconMap[iconType],
          },
        });
      };

      addNode('input', t('resourceCenter.taskInput'), modeLabel ?? '', 0, 0, 'start');
      agents.forEach((agent, index) => {
        const id = `agent-${index}`;
        addNode(id, agent.name, agent.role, (index + 1) * gapX, 0, 'agent');
        graph.addEdge({
          ...edgeAttrs,
          source: { cell: index === 0 ? 'input' : `agent-${index - 1}`, port: 'output' },
          target: { cell: id, port: 'input' },
          connector: { name: 'smooth' },
          labels: [
            {
              position: { distance: 0.5, offset: { x: 0, y: -12 } },
              attrs: {
                text: {
                  text: t('resourceCenter.executeInOrder'),
                  fill: '#6b7280',
                  fontSize: 11,
                },
              },
            },
          ],
        });
      });

      const resultX = (agents.length + 1) * gapX;
      addNode('result', t('resourceCenter.resultSummary'), '', resultX, 0, 'end');
      graph.addEdge({
        ...edgeAttrs,
        source: { cell: `agent-${agents.length - 1}`, port: 'output' },
        target: { cell: 'result', port: 'input' },
        connector: { name: 'smooth' },
        labels: [
          {
            position: { distance: 0.5, offset: { x: 0, y: -12 } },
            attrs: {
              text: {
                text: t('resourceCenter.collaborationResult'),
                fill: '#6b7280',
                fontSize: 11,
              },
            },
          },
        ],
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
    } else {
      const height = Math.max(agents.length * gapY, 240);

      const addNode = (id: string, label: string, desc: string, x: number, y: number, iconType: 'start' | 'agent' = 'agent') => {
        graph.addNode({
          id,
          shape: 'cluster-node',
          x,
          y,
          width: CLUSTER_NODE_WIDTH,
          height: CLUSTER_NODE_HEIGHT,
          ports: {
            groups: {
              left: leftPortGroup,
              right: previewRightPortGroup,
            },
            items: [
              { id: 'input', group: 'left', args: { x: 0, y: 26 } },
              { id: 'output', group: 'right', args: { x: CLUSTER_NODE_WIDTH, y: 26 } },
            ],
          },
          data: {
            name: label,
            description: desc,
            icon: iconMap[iconType],
          },
        });
      };
      addNode('cluster', title, modeLabel || '', 0, (height - CLUSTER_NODE_HEIGHT) / 2, 'start');
      agents.forEach((agent, index) => {
        const id = `agent-${index}`;
        addNode(id, agent.name, agent.role, gapX, index * gapY, 'agent');
        graph.addEdge({
          ...edgeAttrs,
          source: { cell: 'cluster', port: 'output' },
          target: { cell: id, port: 'input' },
          connector: { name: 'smooth' },
          labels: [
            {
              position: { distance: 0.5, offset: { x: 0, y: -12 } },
              attrs: {
                text: {
                  text: t('resourceCenter.taskDistribution'),
                  fill: '#6b7280',
                  fontSize: 11,
                },
              },
            },
          ],
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
    }
  }, [agents, title, serial, t, modeLabel]);

  return {
    containerRef,
    zoomIn: () => graphRef.current?.zoom(0.2),
    zoomOut: () => graphRef.current?.zoom(-0.2),
    fit: () => graphRef.current?.zoomToFit({ padding: 24, maxScale: 1 }),
  };
};
