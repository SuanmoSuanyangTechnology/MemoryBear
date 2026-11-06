import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Flex, Form } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem';
import { appSharing } from '@/api/application';
import type { AppSharingForm, AppSharingModalRef, Release } from '../types';
import { formatDateTime } from '@/utils/format'

interface AppSharingModalProps {
  appId: string;
  version: Release | null;
}

const AppSharingModal = forwardRef<AppSharingModalRef, AppSharingModalProps>(({ appId, version }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<AppSharingForm>();
  const catalogEnabled = Form.useWatch(['resource_center', 'enabled'], form);
  // const marketEnabled = Form.useWatch(['community_market', 'enabled'], form);
  const close = () => {
    setVisible(false);
    form.resetFields();
  };
  useImperativeHandle(ref, () => ({
    handleOpen() {
      form.resetFields();
      setVisible(true);
    },
  }));
  const submit = (values: AppSharingForm) => {
    if (loading) return;
    setLoading(true);
    appSharing(appId, { ...values, release_id: version?.id ?? null })
      .then(() => {
        message.success(t('common.operateSuccess'));
        close();
      })
      .finally(() => setLoading(false));
  };
  return (
    <RbModal
      title={t('application.sharingApp')}
      open={visible}
      onCancel={loading ? undefined : close}
      closable={!loading}
      onOk={() => form.submit()}
      confirmLoading={loading}
      width={600}
    >
      <Form
        form={form}
        onFinish={submit}
        initialValues={{
          resource_center: { enabled: false, allow_editable: false },
          // community_market: { enabled: false }
        }}
      >
        {/* Version Info */}
        <div className="rb:rounded-lg rb:bg-gray-50 rb:p-4 rb:mb-6">
          <div className="rb:text-sm rb:font-medium rb:mb-3">{t('application.versionInfo')}</div>
          <Flex gap={48}>
            <div>
              <div className="rb:text-xs rb:text-gray-500 rb:mb-1">{t('application.versionNumber')}</div>
              <div className="rb:text-sm rb:font-medium">{version?.version_name || '-'}</div>
            </div>
            <div>
              <div className="rb:text-xs rb:text-gray-500 rb:mb-1">{t('application.publishedAt')}</div>
              <div className="rb:text-sm rb:font-medium">
                {version?.published_at ? formatDateTime(version.published_at, 'YYYY-MM-DD HH:mm:ss') : '-'}
              </div>
            </div>
            <div>
              <div className="rb:text-xs rb:text-gray-500 rb:mb-1">{t('application.publisher')}</div>
              <div className="rb:text-sm rb:font-medium">{version?.publisher_name || '-'}</div>
            </div>
          </Flex>
        </div>

        <div className="rb:text-sm rb:text-gray-600 rb:mb-4">{t('application.selectShareScope')}</div>

        {/* Resource Center */}
        <div className="rb:border rb:border-gray-200 rb:rounded-lg rb:p-4 rb:mb-3">
          <SwitchFormItem
            title={t('resourceSharing.catalog')}
            desc={t('application.catalogDesc')}
            name={['resource_center', 'enabled']}
          />
          <div className="rb:border-t rb:border-gray-100 rb:my-3" />
          <SwitchFormItem
            title={t('resourceSharing.editable')}
            desc={t('application.editableDesc')}
            name={['resource_center', 'allow_editable']}
            disabled={!catalogEnabled}
          />
        </div>

        {/* Community Market */}
        {/* <div className="rb:border rb:border-gray-200 rb:rounded-lg rb:p-4">
          <SwitchFormItem
            title={t('resourceSharing.market')}
            desc={t('application.marketDesc')}
            name={['community_market', 'enabled']}
          />
        </div>

        {marketEnabled && (
          <Form.Item
            name={['community_market', 'description']}
            label={t('resourceSharing.description')}
            rules={[{ required: true, whitespace: true, message: t('resourceSharing.descriptionRequired') }]}
            className="rb:mt-4"
          >
            <Input.TextArea rows={4} placeholder={t('common.enter')} />
          </Form.Item>
        )} */}
      </Form>
    </RbModal>
  );
});

export default AppSharingModal;
