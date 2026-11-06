import { useMemo } from 'react';
import { Button, Empty, Flex, Tag } from 'antd';
import { useTranslation } from 'react-i18next';
import { useClusterPreview } from './useClusterPreview';

interface ClusterPreviewProps {
  payload: Record<string, unknown>;
  name: string;
}

/** Displays published cluster members without inferring execution rules from free text. */
const ClusterPreview = ({ payload }: ClusterPreviewProps) => {
  const { t } = useTranslation();
  const agents = useMemo(() => {
    if (!Array.isArray(payload.sub_agents)) return [];
    return payload.sub_agents.flatMap((value: unknown) => {
      if (!value || typeof value !== 'object' || !('name' in value) || typeof value.name !== 'string') return [];
      return [{
        name: value.name,
        role: 'description' in value && typeof value.description === 'string'
          ? value.description
          : 'role' in value && typeof value.role === 'string' ? value.role : '',
      }];
    });
  }, [payload.sub_agents]);
  const isSerial = payload.orchestration_mode === 'collaboration';
  const { containerRef, zoomIn, zoomOut, fit } = useClusterPreview(
    agents,
    isSerial ? t('resourceCenter.taskInput') : t(`application.${payload.orchestration_mode}`),
    isSerial,
    isSerial ? t('resourceCenter.serialExecution') : t('resourceCenter.parallel'),
  );
  return (
    <section>
      <Flex align="center" justify="space-between" gap={12}>
        <h3 className="rb:font-semibold">{t('resourceCenter.collaboration')}</h3>
        <span className="rb:text-xs rb:text-gray-600">{t('resourceCenter.agentCount', { count: agents.length })}</span>
      </Flex>
      <Flex gap={8} className="rb:my-3!">
        <Tag color="purple">{t(`application.${payload.orchestration_mode}`)}</Tag>
        <Tag color="blue">{isSerial ? t('resourceCenter.serialExecution') : t('resourceCenter.parallel')}</Tag>
      </Flex>
      {typeof payload.routing_rules === 'string' && payload.routing_rules && (
        <p className="rb:my-3 rb:whitespace-pre-wrap rb:wrap-break-word rb:text-sm rb:text-gray-600">{payload.routing_rules}</p>
      )}
      {agents.length ? (
        <div className="rb:relative rb:mt-4 rb:overflow-hidden rb:rounded-lg rb:border rb:border-gray-200 rb:bg-gray-100">
          <div ref={containerRef} className="rb:h-96 rb:w-full" aria-label={t('resourceCenter.collaboration')} />
          <Flex vertical gap={2} className="rb:absolute rb:bottom-3 rb:left-3">
            <Button size="small" aria-label={t('resourceCenter.zoomIn')} onClick={zoomIn}>+</Button>
            <Button size="small" aria-label={t('resourceCenter.zoomOut')} onClick={zoomOut}>−</Button>
            <Button size="small" onClick={fit}>{t('resourceCenter.fit')}</Button>
          </Flex>
        </div>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    </section>
  );
};

export default ClusterPreview;
