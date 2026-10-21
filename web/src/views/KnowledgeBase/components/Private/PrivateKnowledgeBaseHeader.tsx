import { useEffect, useState, type FC } from 'react';
import { Flex, Tooltip, type MenuProps, Divider } from 'antd';
import { useTranslation } from 'react-i18next';

import MoreDropdown from '@/components/MoreDropdown';
import Tag from '@/components/Tag';
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';
import { formatDateTime } from '@/utils/format'

interface PrivateKnowledgeBaseHeaderProps {
  knowledgeBase: KnowledgeBaseListItem;
  onCopyKnowledgeBaseId: () => void;
  onKnowledgeBaseStatusChange: (checked: boolean) => void;
  onShareKnowledgeBase: () => void;
  onOpenRecallTest: () => void;
  onOpenMetadata: () => void;
  onOpenKnowledgeBaseSettings: () => void;
  onBatchDownloadKnowledgeBase: () => void;
  onExportKnowledgeBaseQa: () => void;
}

const PrivateKnowledgeBaseHeader: FC<PrivateKnowledgeBaseHeaderProps> = ({
  knowledgeBase,
  onCopyKnowledgeBaseId,
  onKnowledgeBaseStatusChange,
  onShareKnowledgeBase,
  onOpenRecallTest,
  onOpenMetadata,
  onOpenKnowledgeBaseSettings,
  onBatchDownloadKnowledgeBase,
  onExportKnowledgeBaseQa,
}) => {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(knowledgeBase.status === 1);

  useEffect(() => {
    setEnabled(knowledgeBase.status === 1);
  }, [knowledgeBase.status]);

  const handleStatusToggle = () => {
    const nextEnabled = !enabled;
    setEnabled(nextEnabled);
    onKnowledgeBaseStatusChange(nextEnabled);
  };

  const operationItems: NonNullable<MenuProps['items']> = [
    {
      key: 'share',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/share.svg')]" />,
      label: t('knowledgeBase.share'),
      onClick: onShareKnowledgeBase,
    },
    {
      key: 'recallTest',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/recallTest.svg')]" />,
      label: t('knowledgeBase.recallTest'),
      onClick: onOpenRecallTest,
    },
    {
      key: 'metadata',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/metadata.svg')]" />,
      label: t('knowledgeBase.metadata.label'),
      onClick: onOpenMetadata,
    },
    {
      key: 'settings',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/setting.svg')]" />,
      label: `${t('knowledgeBase.knowledgeBase')} ${t('knowledgeBase.setting')}`,
      onClick: onOpenKnowledgeBaseSettings,
    },
    { type: 'divider' },
    {
      key: 'batchDownload',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/export.svg')]" />,
      label: t('knowledgeBase.batchDownload'),
      onClick: onBatchDownloadKnowledgeBase,
    },
    {
      key: 'exportQa',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/export.svg')]" />,
      label: t('knowledgeBase.exportQa'),
      onClick: onExportKnowledgeBaseQa,
    },
    { type: 'divider' },
    {
      key: 'toggleStatus',
      label: t(enabled ? 'common.disable' : 'common.enable'),
      onClick: handleStatusToggle,
    },
  ];

  return (
    <Flex align="center" justify="space-between" gap={12} className="rb:pl-3! rb:pt-3! rb:pr-6! rb:overflow-hidden!">
      <Flex vertical gap={4} className="rb:flex-1! rb:overflow-hidden!">
        <Flex align="center" gap={8}>
          <Tooltip title={knowledgeBase.name} placement="topLeft">
            <span className="rb:truncate rb:min-w-0 rb:inline-flex-1! rb:text-[16px] rb:font-medium rb:text-[#171719]">{knowledgeBase.name}</span>
          </Tooltip>
          <Tag color={enabled ? 'success' : 'default'} className="rb:shrink-0!">
            {t(enabled ? 'common.enable' : 'common.disable')}
          </Tag>
        </Flex>
        <Flex align="center" className="rb:text-gray-600 rb:text-[12px]">
          <Flex gap={4} align="center" className="rb:cursor-pointer rb:hover:text-gray-900 rb:group" onClick={onCopyKnowledgeBaseId}>
            ID: {knowledgeBase.id}
            <div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/copy.svg')] rb:shrink-0 rb:group-hover:bg-[url('@/assets/images/common/copy_dark.svg')]" />
          </Flex>
          <Divider type="vertical" className="rb:mx-4!" />
          <span>{t('knowledgeBase.created')}{t('knowledgeBase.time')}: {formatDateTime(knowledgeBase.created_at) || '-'}</span>
          <Divider type="vertical" className="rb:mx-4!" />
          <span>{t('knowledgeBase.updated')}{t('knowledgeBase.time')}: {formatDateTime(knowledgeBase.updated_at) || '-'}</span>
        </Flex>
      </Flex>

      <div className="rb:shrink-0">
        <MoreDropdown
          items={operationItems}
          placement="bottomRight"
        />
      </div>
    </Flex>
  );
};

export default PrivateKnowledgeBaseHeader;
