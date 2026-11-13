import { useCallback } from 'react';
import { App } from 'antd';
import { useTranslation } from 'react-i18next';
import { deleteTool } from '@/api/tools';
import { deleteInstallations } from '@/api/resourceCenter';

/**
 * Common hook for tool deletion with confirmation
 * Used by Mcp, Custom, Workflow components
 */
export function useToolDelete(onSuccess?: () => void) {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();

  return useCallback(
    (item: { id: string; name: string; origin: string; external?: { resource_id: string; } | null }) => {
      if (!item.id) return;

      modal.confirm({
        title: t('common.confirmDeleteDesc', { name: item.name }),
        okText: t('common.delete'),
        cancelText: t('common.cancel'),
        okType: 'danger',
        onOk: () => {
          const request = item.origin === 'external'
          ? deleteInstallations('tool', item.id)
          : deleteTool(item.id as string)
          return request
            .then(() => {
              message.success(t('common.deleteSuccess'));
              onSuccess?.();
            });
        },
      });
    },
    [t, modal, message, onSuccess],
  );
}
