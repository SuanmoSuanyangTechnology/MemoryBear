import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { ReactNode } from 'react';

import Tag from '@/components/Tag'

/**
 * Common hook for rendering status tags
 * Used across all tool management components
 */
export function useToolStatus() {
  const { t } = useTranslation();

  return useMemo(() => {
    return (status: string): ReactNode => {
      switch (status) {
        case 'available':
          return <Tag color="success" className="rb:shrink-0!">{t('tool.status.available')}</Tag>;
        case 'unconfigured':
          return <Tag color="default" className="rb:shrink-0!">{t('tool.status.unconfigured')}</Tag>;
        case 'configured_disabled':
          return <Tag color="warning" className="rb:shrink-0!">{t('tool.status.configured_disabled')}</Tag>;
        case 'error':
          return <Tag color="error" className="rb:shrink-0!">{t('tool.status.error')}</Tag>;
        default:
          return null;
      }
    };
  }, [t]);
}
