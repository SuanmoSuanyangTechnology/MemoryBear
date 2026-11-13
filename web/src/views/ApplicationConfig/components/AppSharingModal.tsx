import { forwardRef, useImperativeHandle, useState } from 'react';
import { App, Flex, Form, Tag } from 'antd';
import { useTranslation } from 'react-i18next';
import RbModal from '@/components/RbModal';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem';
import { appSharing, getSharePreview } from '@/api/application';
import type { AppSharingForm, AppSharingModalRef, Release, SharePreviewData } from '../types';
import { formatDateTime } from '@/utils/format'
import RbAlert from '@/components/RbAlert';

interface AppSharingModalProps {
  appId: string;
  version: Release | null;
}

const AppSharingModal = forwardRef<AppSharingModalRef, AppSharingModalProps>(({ appId, version }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [form] = Form.useForm<AppSharingForm>();
  const [confirmVisible, setConfirmVisible] = useState(false);
  const [previewData, setPreviewData] = useState<SharePreviewData | null>(null);
  const catalogEnabled = Form.useWatch(['resource_center', 'enabled'], form);
  const marketEnabled = Form.useWatch(['community_market', 'enabled'], form);
  const close = () => {
    setVisible(false);
    form.resetFields();
    setPreviewData(null);
  };
  useImperativeHandle(ref, () => ({
    handleOpen() {
      form.resetFields();
      setPreviewData(null);
      setVisible(true);
    },
  }));

  const handleSubmit = () => {
    form.validateFields().then((values) => {
      if (marketEnabled) {
        setPreviewLoading(true);
        getSharePreview(appId, version?.id)
          .then((data) => {
            const preview = data as SharePreviewData;
            setPreviewData(preview);
            setConfirmVisible(true);
          })
          .catch(() => {
            message.error(t('resourceSharing.failed'));
          })
          .finally(() => {
            setPreviewLoading(false);
          });
      } else {
        submit(values);
      }
    });
  };

  const submit = (values: AppSharingForm) => {
    if (loading) return;
    setLoading(true);
    appSharing(appId, { ...values, release_id: version?.id ?? null })
      .then(() => {
        message.success(t('common.operateSuccess'));
        close();
        setConfirmVisible(false);
      })
      .finally(() => setLoading(false));
  };

  const handleConfirmSubmit = () => {
    const values = form.getFieldsValue();
    submit(values);
  };

  return (
    <>
      <RbModal
        title={t('application.sharingApp')}
        open={visible}
        onCancel={loading ? undefined : close}
        closable={!loading}
        onOk={handleSubmit}
        okText={marketEnabled ? t('application.submitReview') : undefined}
        confirmLoading={loading || previewLoading}
        width={600}
      >
        <Form
          form={form}
          initialValues={{
            resource_center: { enabled: true, allow_editable: false },
          }}
        >
          {/* Version Info */}
          <div className="rb:rounded-lg rb:bg-gray-50 rb:p-4 rb:mb-6">
            <div className="rb:text-sm rb:font-medium rb:mb-3">{t('application.versionInfo')}</div>
            <Flex gap={48}>
              <div>
                <div className="rb:text-xs rb:text-gray-600 rb:mb-1">{t('application.versionNumber')}</div>
                <div className="rb:text-sm rb:font-medium">{version?.version_name || '-'}</div>
              </div>
              <div>
                <div className="rb:text-xs rb:text-gray-600 rb:mb-1">{t('application.publishedAt')}</div>
                <div className="rb:text-sm rb:font-medium">
                  {version?.published_at ? formatDateTime(version.published_at, 'YYYY-MM-DD HH:mm:ss') : '-'}
                </div>
              </div>
              <div>
                <div className="rb:text-xs rb:text-gray-600 rb:mb-1">{t('application.publisher')}</div>
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
              disabled={true}
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
          <div className="rb:border rb:border-gray-200 rb:rounded-lg rb:p-4">
            <SwitchFormItem
              title={t('resourceSharing.market')}
              desc={t('application.marketDesc')}
              name={['community_market', 'enabled']}
            />
          </div>
        </Form>
      </RbModal>

      {/* Confirmation Modal */}
      <RbModal
        title={t('application.shareConfirmTitle')}
        open={confirmVisible}
        onCancel={() => setConfirmVisible(false)}
        onOk={handleConfirmSubmit}
        okText={t('application.confirmAndSubmit')}
        cancelText={t('application.backToEdit')}
        confirmLoading={loading}
        okButtonProps={{ disabled: !previewData?.can_submit_market }}
        width={600}
      >
        {!previewData?.can_submit_market
          ? (
            <Flex vertical gap={8}>
              {previewData?.blockers.map((item, index) => (
                <RbAlert key={index} color="red">{item.message}</RbAlert>
              ))}
            </Flex>
          )
          : (
            <>
              {previewData && (
                <RbAlert className="rb:mb-6!">
                  {t('application.shareConfirmAlert', { count: previewData.carried_dependencies.filter(d => d.disposition === 'bundled').length })}
                </RbAlert>
              )}

              {previewData && previewData.carried_dependencies.filter(d => d.disposition === 'bundled').length > 0 && (
                <div className="rb:mb-4">
                  <div className="rb:text-[14px] rb:font-medium rb:mb-1">
                    {t('application.bundledDependencies')} <span className="rb:text-gray-600 rb:font-normal rb:text-[12px]">{t('application.items', { count: previewData.carried_dependencies.filter(d => d.disposition === 'bundled').length })}</span>
                  </div>
                  <div className="rb:text-[12px] rb:text-gray-600 rb:mb-3">{t('application.bundledDependenciesDesc')}</div>
                  <div className="rb:flex rb:flex-col rb:gap-2">
                    {previewData.carried_dependencies.filter(d => d.disposition === 'bundled').map((dep, index) => (
                      <div key={index} className="rb:p-3 rb:border rb:border-gray-200 rb:rounded-lg">
                        <Flex align="center" gap={8} className="rb:mb-1">
                          <span className="rb:text-[13px] rb:font-medium">{dep.name}</span>
                          <Tag className="rb:text-[12px]">{t(`application.carriedKind_${dep.kind}`)}</Tag>
                        </Flex>
                        {dep.methods && dep.methods.length > 0 && (
                          <div className="rb:text-[12px] rb:text-gray-600">
                            {t('application.requiredMethods')}: {dep.methods.join(', ')}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {previewData && previewData.carried_dependencies.filter(d => d.disposition !== 'bundled').length > 0 && (
                <div className="rb:mb-4">
                  <div className="rb:text-[14px] rb:font-medium rb:mb-1">
                    {t('application.privateNotCarried')} <span className="rb:text-gray-600 rb:font-normal rb:text-[12px]">{t('application.items', { count: previewData.carried_dependencies.filter(d => d.disposition !== 'bundled').length })}</span>
                  </div>
                  <div className="rb:text-[12px] rb:text-gray-600 rb:mb-3">{t('application.privateNotCarriedDesc')}</div>
                  <div className="rb:flex rb:flex-col rb:gap-2">
                    {previewData.carried_dependencies.filter(d => d.disposition !== 'bundled').map((dep, index) => (
                      <div key={index} className="rb:p-3 rb:border rb:border-gray-200 rb:rounded-lg">
                        <Flex align="center" gap={8} className="rb:mb-1">
                          <span className="rb:text-[13px] rb:font-medium">{dep.name}</span>
                          <Tag className="rb:text-[12px]">{t(`application.carriedKind_${dep.kind}`)}</Tag>
                        </Flex>
                        {dep.methods && dep.methods.length > 0 && (
                          <div className="rb:text-[12px] rb:text-gray-600">
                            {t('application.requiredMethods')}: {dep.methods.join(', ')}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {previewData && previewData.builtin_tools.length > 0 && (
                <div className="rb:mb-4">
                  <div className="rb:text-[14px] rb:font-medium rb:mb-1">
                    {t('application.builtinTools')} <span className="rb:text-gray-600 rb:font-normal rb:text-[12px]">{t('application.items', { count: previewData.builtin_tools.length })}</span>
                  </div>
                  <div className="rb:text-[12px] rb:text-gray-600 rb:mb-3">{t('application.builtinToolsDesc')}</div>
                  <div className="rb:flex rb:flex-col rb:gap-2">
                    {previewData.builtin_tools.map((tool, index) => (
                      <div key={index} className="rb:p-3 rb:border rb:border-gray-200 rb:rounded-lg">
                        <Flex align="center" gap={8}>
                          <span className="rb:text-[13px] rb:font-medium">{tool.name}</span>
                          <Tag className="rb:text-[12px]">{t('application.builtinTools')}</Tag>
                        </Flex>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="rb:text-[13px] rb:text-gray-600 rb:mb-4">{t('application.notCarry')}</div>

              <div className="rb:p-4 rb:bg-gray-50 rb:rounded-lg rb:border rb:border-gray-200">
                <div className="rb:text-[13px] rb:text-gray-600">{t('application.shareConfirmFooter')}</div>
              </div>
            </>
          )
        }
      </RbModal>
    </>
  );
});

export default AppSharingModal;
