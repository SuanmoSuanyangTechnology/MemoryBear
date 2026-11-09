/**
 * Release Modal
 * Allows publishing a new version of the application
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { ReleaseModalData, ReleaseModalRef, Release } from '../types'
import RbModal from '@/components/RbModal'
import { publishRelease, getCurrentRelease } from '@/api/application'
import { getFileLink } from '@/api/fileStorage'
import type { Application } from '@/views/ApplicationManagement/types'
import UploadImages from '@/components/Upload/UploadImages'
import { stringRegExp } from '@/utils/validator';
import { getAppAssetsCategoriesUrl } from '@/api/resourceCenter';
import CustomSelect from '@/components/CustomSelect';
import RichTextEditor from '@/components/RichTextEditor';

const FormItem = Form.Item;

/**
 * Component props
 */
interface ReleaseModalProps {
  /** Callback to refresh release list */
  refreshTable: () => void;
  /** Application data */
  data: Application
}

/**
 * Modal for publishing new application versions
 */
const ReleaseModal = forwardRef<ReleaseModalRef, ReleaseModalProps>(({
  refreshTable,
  data
}, ref) => {
  const { t, i18n } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ReleaseModalData>();
  const [loading, setLoading] = useState(false)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = (release?: Release | null) => {
    setVisible(true);
    getCurrentRelease(data.id)
      .then(res => {
        const response = res as { name: string; icon: any; }
        form.setFieldsValue({
          name: response.name,
          icon: response.icon ? { url: response.icon, uid: response.icon, status: 'done', name: 'icon' } : undefined,
          categories: release?.categories || [],
          usage_examples: release?.usage_examples,
          setup_steps: release?.setup_steps
        })
      })
  };
  /** Publish new release */
  const handleSave = () => {
    form.validateFields()
      .then(() => {
        setLoading(true)
        const { icon, ...rest } = form.getFieldsValue()
        const formData: ReleaseModalData = {
          ...rest
        }
        if (icon?.response?.data.file_id) {
          getFileLink(icon?.response?.data.file_id).then(res => {
            const logoRes = res as { url: string }
            formData.icon = logoRes.url
            handleUpdate(formData)
          }).catch(() => {
            handleUpdate(formData)
          })
        } else {
          formData.icon = icon.url
          handleUpdate(formData)
        }
      })
      .catch(({ errorFields }) => {
        form.scrollToField(errorFields[0].name, { behavior: 'smooth', block: 'center', focus: true })
      })
  }
  const handleUpdate = (formData: ReleaseModalData) => {
    publishRelease(data.id, formData)
      .then(() => {
        handleClose()
        refreshTable()
      })
      .finally(() => {
        setLoading(false)
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <>
      <RbModal
        title={t('application.releaseNewVersion')}
        open={visible}
        onCancel={handleClose}
        okText={t('application.release')}
        onOk={handleSave}
        confirmLoading={loading}
      >
        <Form
          form={form}
          layout="vertical"
          scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
        >
          {/* Version name */}
          <FormItem
            name="version_name"
            label={t('application.versionName')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
            extra={t('application.versionNameTip')}
          >
            <Input placeholder={t('common.enter')} />
          </FormItem>
          {/* Version description */}
          <FormItem
            name="release_notes"
            label={t('application.versionDescription')}
            rules={[
              { required: true, message: t('common.pleaseEnter') },
            ]}
            extra={t('application.versionDescriptionTip')}
          >
            <Input.TextArea placeholder={t('common.enter')} />
          </FormItem>

          <FormItem
            name="name"
            label={t('application.customTitle')}
            tooltip={t('application.customTitleTip')}
            extra={t('application.customTitleTip')}
            rules={[
              { max: 50 },
              { pattern: stringRegExp, message: t('common.nameInvalid') },
            ]}
          >
            <Input placeholder={t('application.customTitlePlaceholder')} />
          </FormItem>
          <Form.Item
            name="icon"
            label={t('application.customIcon')}
            valuePropName="fileList"
            tooltip={t('application.customIconTip')}
            extra={t('application.customIconDesc')?.split('\n').map((vo, index) => <div key={index}>{vo}</div>)}
            rules={[
              { required: true, message: t('common.pleaseSelect') },
            ]}
          >
            <UploadImages fileSize={2} />
          </Form.Item>
          <FormItem
            name="categories"
            label={t('application.category')}
            rules={[
              { required: true, message: t('common.pleaseSelect') },
            ]}
          >
            <CustomSelect
              url={getAppAssetsCategoriesUrl}
              hasAll={false}
              mode="multiple"
              valueKey="code"
              labelKey={i18n.language === 'zh' ? 'name' : 'name_en'}
            />
          </FormItem>
          {(data.type === 'agent' || data.type === 'multi_agent') ? (
            <Form.List
              name="usage_examples"
              initialValue={['', '', '']}
              rules={[
                {
                  validator: async (_, value) => {
                    if (!value || value.length < 3) {
                      return Promise.reject(new Error(t('application.usageExamplesMin')));
                    }
                  },
                },
              ]}
            >
              {(fields, { add, remove }, { errors }) => (
                <>
                  <FormItem label={t('application.usageExamples')} required>
                    <Flex vertical gap={12}>
                      {fields.map((field) => (
                        <Flex key={field.key} align="center" gap={12}>
                          <FormItem
                            {...field}
                            rules={[
                              { required: true, message: t('common.pleaseEnter') },
                              { max: 500, message: t('application.usageExampleMaxLength', { max: 500 }) },
                            ]}
                            className="rb:flex-1!"
                          >
                            <Input.TextArea
                              placeholder={`${t('application.usageExamplePrefix')}${field.key + 1}: ${t('application.usageExamplePlaceholder')}`}
                              maxLength={500}
                              showCount
                            />
                          </FormItem>
                          <div onClick={() => fields.length > 3 && remove(field.name)}
                            className={clsx("rb:size-4.5 rb:shrink-0! rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')]", {
                              "rb:cursor-pointer rb:hover:bg-[url('@/assets/images/common/delete_red.svg')]": fields.length > 3,
                              'rb:cursor-not-allowed rb:opacity-65': fields.length <= 3
                            })}
                          />
                        </Flex>
                      ))}
                      {fields.length < 5 && (
                        <Button
                          type="dashed"
                          onClick={() => add()}
                          block
                        >
                          + {t('application.addUsageExample', { count: fields.length })}
                        </Button>
                      )}
                    </Flex>
                    <Form.ErrorList errors={errors} />
                    <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>
                      {t('application.usageExamplesHint')}
                    </div>
                  </FormItem>
                </>
              )}
            </Form.List>
          ) : (
            <FormItem
              name="setup_steps"
              label={t('application.setupSteps')}
              rules={[
                { required: true, message: t('application.setupStepsRequired') },
              ]}
              extra={t('application.setupStepsHint')}
              // valuePropName="value"
              // trigger="onChange"
            >
              <RichTextEditor
                placeholder={t('application.setupStepsPlaceholder')}
                maxLength={2000}
              />
            </FormItem>
          )}
        </Form>
      </RbModal>
    </>
  );
});

export default ReleaseModal;