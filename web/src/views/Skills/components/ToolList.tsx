import type { FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex } from 'antd';

import Empty from '@/components/Empty';
import Tag from '@/components/Tag';
import type { LegacySkill } from '../types';

interface ToolListProps {
  value?: LegacySkill['tools'];
}

/** 展示技能关联工具，直接使用传入的工具信息。 */
const ToolList: FC<ToolListProps> = ({ value = [] }) => {
  const { t } = useTranslation();

  return (
    <section aria-label={t('application.toolConfiguration')}>
      <div className="rb:mb-2">{t('application.toolConfiguration')}</div>
      {value.length === 0 ? (
        <div className="rb-border rb:rounded-xl rb:pt-4 rb:pb-6">
          <Empty size={88} />
        </div>
      ) : (
        <Flex
          vertical
          gap={12}
          className="rb:grid! rb:grid-cols-2 rb:gap-3"
        >
          {value.map((item, index) => (
            <Flex
              key={`${item.tool_id}-${item.operation ?? ''}-${index}`}
              align="center"
              justify="space-between"
              className="rb:py-2.5! rb:pl-4! rb:pr-3! rb-border rb:rounded-lg"
            >
              <div>
                <div className="rb:font-medium rb:leading-4">
                  {item.tool_info?.name || item.operation || item.tool_id}
                </div>
                {!item.tool_missing && item.tool_info?.is_active !== undefined && (
                  <Tag color={item.tool_info.is_active ? 'success' : 'error'} className="rb:mt-1">
                    {item.tool_info.is_active ? t('common.enable') : t('common.deleted')}
                  </Tag>
                )}
                {item.tool_missing && (
                  <Tag color="error" className="rb:mt-1">
                    {t('skills.unavailable')}
                  </Tag>
                )}
              </div>
            </Flex>
          ))}
        </Flex>
      )}
    </section>
  );
};

export default ToolList;
