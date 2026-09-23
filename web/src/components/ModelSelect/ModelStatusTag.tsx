import { type FC } from 'react';
import { WarningFilled } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import Tag from '@/components/Tag';
import type { Model } from '@/views/ModelManagement/types';

const ModelStatusTag: FC<{ model: Model }> = ({ model }) => {
  const { t } = useTranslation();

  return model && model.is_deprecated
    ? <Tag color="default">{t('modelNew.deprecated')}</Tag>
    : model && typeof model.is_available !== 'undefined' && !model.is_available
    ? <Tag color="error"><WarningFilled className="rb:mr-1" />{t('common.statusDisabled')}</Tag>
    : null
}

export default ModelStatusTag