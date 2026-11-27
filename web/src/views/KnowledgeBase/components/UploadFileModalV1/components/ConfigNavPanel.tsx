import { Flex } from 'antd';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import { configSections } from '../constants';
import type { ConfigSectionKey } from '../types';

interface ConfigNavPanelProps {
  activeSection: ConfigSectionKey;
  onChange: (key: ConfigSectionKey) => void;
}

/** Middle column of the modal: vertical section navigation of the parsing config. */
const ConfigNavPanel = ({ activeSection, onChange }: ConfigNavPanelProps) => {
  const { t } = useTranslation();

  return (
    <Flex
      vertical
      aria-label={t('knowledgeBase.uploadModal.parsingConfigTitle')}
      className="rb:w-52 rb:shrink-0 rb:h-full! rb:overflow-hidden! rb:border-r rb:border-gray-200 rb:bg-gray-50"
    >
      <h3 className="rb:h-15.5! rb:p-4! rb-border-b rb:text-[16px] rb:font-medium rb:shrink-0">
        {t('knowledgeBase.uploadModal.parsingConfigTitle')}
      </h3>
      <Flex vertical gap={8} className="rb:p-3! rb:flex-1! rb:overflow-y-auto!">
        {configSections.map((section) => (
          <Flex
            key={section.key}
            gap={8}
            aria-current={activeSection === section.key ? 'true' : undefined}
            onClick={() => onChange(section.key)}
            className={clsx(
              'rb:rounded-lg rb:p-3! rb:text-left rb:cursor-pointer',
              {
                'rb:bg-[#eceef1]': activeSection === section.key,
                'rb:hover:bg-[#F2F4F7]': activeSection !== section.key,
              },
            )}
          >
            <span className="rb:mt-0.5 rb:shrink-0">{section.icon}</span>
            <div className="rb:min-w-0 rb:flex-1">
              <div className="rb:text-sm rb:font-medium">{t(section.labelKey)}</div>
              <div className="rb:text-xs rb:text-gray-600 rb:mt-1">{t(section.descKey)}</div>
            </div>
          </Flex>
        ))}
      </Flex>
    </Flex>
  );
};

export default ConfigNavPanel;
