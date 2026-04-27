import { App } from 'antd';
import { useTranslation } from 'react-i18next';

interface DeleteConfirmOptions {
  name: string;
  onOk: () => Promise<unknown> | void;
}

/**
 * Hook for standardized delete confirmation dialog.
 * Extracts the repeated modal.confirm pattern used across all management views.
 */
const useDeleteConfirm = () => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();

  const confirm = ({ name, onOk }: DeleteConfirmOptions) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: async () => {
        await onOk();
        message.success(t('common.deleteSuccess'));
      },
    });
  };

  return confirm;
};

export default useDeleteConfirm;
