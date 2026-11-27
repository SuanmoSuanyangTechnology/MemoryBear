import { Button, Flex } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import type { Model } from '@/views/ModelManagement/types';
import {
  AdvancedSection,
  ChunkSection,
  MultimodalSection,
  ParsingSection,
} from './ConfigSections';
import { configSections } from '../constants';
import type { ConfigSectionKey } from '../types';

interface ConfigContentPanelProps {
  activeSection: ConfigSectionKey;
  customModels: Record<string, Model[]>;
  hasFiles: boolean;
  isRechunkMode: boolean;
  isParentChildMode: boolean | null;
  loading: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

/** Right column of the modal: the current section's config form, title, and bottom action buttons. */
const ConfigContentPanel = ({
  activeSection,
  customModels,
  hasFiles,
  isRechunkMode,
  isParentChildMode,
  loading,
  onClose,
  onConfirm,
}: ConfigContentPanelProps) => {
  const { t } = useTranslation();
  const currentSection = configSections.find((section) => section.key === activeSection) ?? configSections[0];

  return (
    <Flex vertical gap={12} className="rb:min-w-0! rb:flex-1!">
      <Flex align="flex-start" justify="space-between" className="rb:px-6! rb:pt-4! rb:pb-3! rb:shrink-0">
        <div>
          <h3 className="rb:text-[16px] rb:font-semibold">{t(currentSection.labelKey)}</h3>
          <p className="rb:mt-1! rb:text-xs rb:text-gray-600">{t(currentSection.detailKey)}</p>
        </div>
        <Button type="text" icon={<CloseOutlined />} aria-label={t('common.cancel')} onClick={onClose} />
      </Flex>

      <div className="rb:min-w-0 rb:flex-1 rb:overflow-auto rb:px-6 rb:pb-6 rb:[&>section>h3]:hidden rb:[&>section>p]:hidden">
        <ParsingSection active={activeSection === 'parsing'} />
        <ChunkSection active={activeSection === 'chunk'} isParentChildMode={isParentChildMode} />
        <MultimodalSection active={activeSection === 'multimodal'} customModels={customModels} />
        <AdvancedSection active={activeSection === 'advanced'} />
      </div>

      <Flex justify="flex-end" gap={8} className="rb:border-t rb:border-gray-200 rb:px-6! rb:py-3! rb:shrink-0">
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="primary" disabled={!hasFiles} loading={loading} onClick={onConfirm}>
          {isRechunkMode ? t('knowledgeBase.rechunking') : t('knowledgeBase.confirmUpload')}
        </Button>
      </Flex>
    </Flex>
  );
};

export default ConfigContentPanel;
