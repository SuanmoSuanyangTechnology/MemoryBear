import { useMemo } from 'react';
import { Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import { useWorkflowPreview } from '@/views/Workflow/hooks/useWorkflowPreview';

interface WorkflowPreviewProps {
  payload: Record<string, unknown>;
}

const record = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' ? value as Record<string, unknown> : {}
);

/** Renders the published workflow snapshot with the shared read-only renderer. */
const WorkflowPreview = ({ payload }: WorkflowPreviewProps) => {
  const { t } = useTranslation();
  const { nodes, edges } = useMemo(() => {
    const config = Array.isArray(payload.nodes) ? payload : record(payload.workflow ?? payload.config);
    const nodes = (Array.isArray(config.nodes) ? config.nodes : []).flatMap((value, index) => {
      const node = record(value);
      const position = record(node.position);
      if (typeof node.id !== 'string' || typeof node.type !== 'string') return [];
      return [{
        id: node.id,
        type: node.type,
        config: record(node.config),
        parentId: typeof node.parent_id === 'string' ? node.parent_id : undefined,
        branches: (Array.isArray(node.branches) ? node.branches : []).flatMap(value => {
          const branch = record(value);
          return typeof branch.label === 'string'
            ? [{ label: branch.label, name: typeof branch.name === 'string' ? branch.name : branch.label }]
            : [];
        }),
        name: typeof node.name === 'string' ? node.name : node.type,
        x: typeof position.x === 'number' ? position.x : index * 280,
        y: typeof position.y === 'number' ? position.y : 0,
      }];
    });
    const ids = new Set(nodes.map(node => node.id));
    const edges = (Array.isArray(config.edges) ? config.edges : []).flatMap(value => {
      const edge = record(value);
      if (typeof edge.source !== 'string' || typeof edge.target !== 'string') return [];
      if (!ids.has(edge.source) || !ids.has(edge.target)) return [];
      return [{
        source: edge.source,
        target: edge.target,
        label: typeof edge.label === 'string' ? edge.label : undefined,
        type: typeof edge.type === 'string' ? edge.type : undefined,
      }];
    });
    return { nodes, edges };
  }, [payload]);
  const { containerRef, zoomIn, zoomOut, fit } = useWorkflowPreview(nodes, edges);

  return (
    <div className="rb:relative rb:mt-4 rb:overflow-hidden rb:rounded-lg rb:border rb:border-gray-200 rb:bg-gray-100">
      <div ref={containerRef} className="rb:h-96 rb:w-full rb:[&_.x6-node]:pointer-events-none" aria-label={t('resourceCenter.flowView')} />
      <Flex vertical gap={2} className="rb:absolute rb:bottom-3 rb:left-3">
        <Button size="small" aria-label={t('resourceCenter.zoomIn')} onClick={zoomIn}>+</Button>
        <Button size="small" aria-label={t('resourceCenter.zoomOut')} onClick={zoomOut}>−</Button>
        <Button size="small" onClick={fit}>{t('resourceCenter.fit')}</Button>
      </Flex>
    </div>
  );
};

export default WorkflowPreview;
