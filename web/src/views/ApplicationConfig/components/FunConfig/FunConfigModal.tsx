/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-13 17:20:30
 */
/**
 * Copy Application Modal
 * Allows users to duplicate an existing application with a new name
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { FunConfigModalRef } from '../../types'
import RbModal from '@/components/RbModal'
import type { FunConfigForm } from '../../types'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import FileUploadSettingModal from './FileUploadSettingModal'

const FormItem = Form.Item;

interface FunConfigModalProps {
  refresh: (value: FunConfigForm) => void;
}

/**
 * Modal for copying applications
 */
const FunConfigModal = forwardRef<FunConfigModalRef, FunConfigModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FunConfigForm>();
  const [loading, setLoading] = useState(false)
  const values = Form.useWatch([], form)
  const fileUploadSettingModalRef = useRef<any>(null)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  /** Open modal */
  const handleOpen = (initValue: FunConfigForm) => {
    setVisible(true);
    form.setFieldsValue(initValue)
  };
  /** Copy application with new name */
  const handleSave = () => {
    setVisible(false);
    setLoading(true)
    const values = form.getFieldsValue()
    refresh(values)
  }

  const handleOpenSettings = () => {
    fileUploadSettingModalRef.current?.handleOpen(values)
  }

  const handleSaveSettings = (settings: any) => {
    form.setFieldsValue(settings)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  return (
    <>
      <RbModal
        title={t('application.funConfig')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.copy')}
        onOk={handleSave}
        confirmLoading={loading}
      >
        <Form
          form={form}
          layout="vertical"
        >
          <Flex vertical gap={12}>
            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t(`memoryConversation.web_search`)}
                name={['web_search', "enabled"]}
              />
            </div>

            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t('application.textTranfer')}
                name={['textTranfer', "enabled"]}
                desc={t('application.textTranferDesc')}
              />
            </div>

            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t('application.fileUpload')}
                name={['fileUpload', "enabled"]}
                desc={values?.fileUpload?.enabled ? undefined : t('application.fileUploadDesc')}
              />
              {values?.fileUpload?.enabled && values?.fileTypes?.length > 0 ? <>
                <div className="rb:grid rb:grid-cols-3 rb:gap-2 rb:text-[12px] rb:text-[#5B6167]">
                  <div>{t(`application.supportedTypes`)}</div>
                  <div>{t('application.maxCount')}</div>
                  <div>{t('application.singleMaxSize')}</div>
                </div>
                {values?.fileTypes?.filter(item => item.enabled).map(item => (
                  <div key={item.type} className="rb:grid rb:grid-cols-3 rb:gap-2">
                    <div>{t(`application.${item.type}`)}</div>
                    <div>{item.maxCount} {t('application.unix')}</div>
                    <div>{item.maxSize} MB</div>
                  </div>
                ))}
                <Button block onClick={handleOpenSettings}>{t('application.setting')}</Button>
              </> : null}
              <FormItem name="fileTypes" noStyle hidden></FormItem>
              <FormItem name="uploadType" noStyle hidden></FormItem>
            </div>
          </Flex>
        </Form>
      </RbModal>

      <FileUploadSettingModal
        ref={fileUploadSettingModalRef}
        onSave={handleSaveSettings}
      />
    </>
  );
});

export default FunConfigModal;