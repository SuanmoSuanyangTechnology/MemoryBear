import { forwardRef, useImperativeHandle, useState, } from 'react';
import { Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal'
import RbAlert from '@/components/RbAlert';
import type { ReembedInfo } from './EmbeddingAlert';

export interface EmbeddingRebuildModalRef {
  handleOpen: () => void;
}

interface EmbeddingRebuildModalProps {
  reembedInfo?: ReembedInfo | null;
  handleViewDetail?: () => void;
  refresh?: () => void;
}

type OverviewDisplayMode = 'queued' | 'runningFailed' | 'running' | 'succeeded' | 'failed'

const getOverviewDisplayMode = (reembedInfo: ReembedInfo): OverviewDisplayMode => {
  const { status, end_users: counts } = reembedInfo

  if (status === 'pending') {
    return 'queued'
  }
  if (status !== 'running') {
    return status
  }
  if (counts.queued > 0 && !counts.running && !counts.failed) {
    return 'queued'
  }
  if (counts.failed > 0) {
    return 'runningFailed'
  }
  return 'running'
}

const EmbeddingRebuildModal = forwardRef<EmbeddingRebuildModalRef, EmbeddingRebuildModalProps>(({
  reembedInfo,
  handleViewDetail,
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);

  const handleClose = () => {
    setVisible(false);
    refresh?.();
  };
  const handleOpen = () => {
    setVisible(true);
  };
  const handleView = () => {
    handleClose()
    handleViewDetail?.()
  }
  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  if (!reembedInfo || !reembedInfo?.status) {
    return null
  }

  const counts = reembedInfo.end_users
  const displayMode = getOverviewDisplayMode(reembedInfo)
  const overviewConfigs = {
    queued: {
      color: 'orange' as const,
      title: t('space.reembedding.overview.queuedTitle'),
      primary: t('space.reembedding.overview.waitingTasks', { queued: counts.queued || 0 }),
      secondary: t('space.reembedding.overview.completedTasks', { succeeded: counts.succeeded || 0 }),
      impacts: [
        t('space.reembedding.overview.queuedImpactCurrent'),
        t('space.reembedding.overview.queuedImpactStarted'),
      ],
    },
    runningFailed: {
      color: 'red' as const,
      title: t('space.reembedding.overview.incompleteTitle'),
      primary: t('space.reembedding.overview.failedTasks', { failed: counts.failed || 0 }),
      secondary: t('space.reembedding.overview.mixedProgress', {
        succeeded: counts.succeeded || 0,
        running: counts.running || 0,
        queued: counts.queued || 0,
      }),
      impacts: [
        t('space.reembedding.overview.failedImpactSearch'),
        t('space.reembedding.overview.failedImpactRetry'),
      ],
    },
    running: {
      color: 'orange' as const,
      title: t('space.reembedding.overview.runningTitle'),
      primary: t('space.reembedding.overview.runningTasks', { running: counts.running || 0 }),
      secondary: t('space.reembedding.overview.runningProgress', {
        queued: counts.queued || 0,
        succeeded: counts.succeeded || 0,
      }),
      impacts: [
        t('space.reembedding.overview.runningImpactVector'),
        t('space.reembedding.overview.runningImpactFallback'),
        t('space.reembedding.overview.runningImpactOthers'),
      ],
    },
    succeeded: {
      color: 'green' as const,
      title: t('space.reembedding.overview.succeededTitle'),
      primary: t('space.reembedding.overview.succeededTasks', { succeeded: counts.succeeded || 0 }),
      secondary: t('space.reembedding.overview.vectorSearchRestored'),
      impacts: [
        t('space.reembedding.overview.succeededImpactIndexed'),
        t('space.reembedding.overview.succeededImpactRestored'),
      ],
    },
    failed: {
      color: 'red' as const,
      title: t('space.reembedding.overview.partialFailedTitle'),
      primary: t('space.reembedding.overview.failedResult', {
        succeeded: counts.succeeded || 0,
        failed: counts.failed || 0,
      }),
      secondary: t('space.reembedding.overview.roundEnded'),
      impacts: [
        t('space.reembedding.overview.partialImpactSuccess'),
        t('space.reembedding.overview.partialImpactFailed'),
        t('space.reembedding.overview.partialImpactRetry'),
      ],
    },
  }
  const overviewConfig = overviewConfigs[displayMode]

  if (!overviewConfig) {
    return null
  }

  return (
    <RbModal
      title={<div>
        {overviewConfig?.title}
        <div className="rb:text-gray-600 rb:text-[12px] rb:font-normal rb:leading-4 rb:mt-1">
          {t('space.reembedding.overview.description')}
        </div>
      </div>}
      open={visible}
      onCancel={handleClose}
      cancelText={t('space.reembedding.overview.acknowledge')}
      okText={t('space.reembedding.overview.viewTaskDetails')}
      onOk={handleView}
    >
      <RbAlert color={overviewConfig.color}>
        <Flex align="center" justify="space-between" className="rb:w-full!">
          <span className="rb:font-medium">{overviewConfig.primary}</span>
          {overviewConfig.secondary}
        </Flex>
      </RbAlert>

      <div className="rb:bg-gray-100 rb:rounded-xl rb:p-3 rb:mt-3 rb:text-[12px]">
        <div className="rb:font-medium">{t('space.reembedding.overview.impactTitle')}</div>
        <ul className="rb:list-disc rb:pl-3 rb:text-gray-600">
          {overviewConfig.impacts.map((impact) => (
            <li key={impact}>{impact}</li>
          ))}
        </ul>
      </div>
    </RbModal>
  );
});

export default EmbeddingRebuildModal;
