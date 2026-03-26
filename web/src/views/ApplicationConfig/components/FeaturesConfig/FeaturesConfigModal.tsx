/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-24 10:59:37
 */
/**
 * Copy Application Modal
 * Allows users to duplicate an existing application with a new name
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Button, Flex } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { FeaturesConfigModalRef, FeaturesConfigForm } from '../../types'
import RbModal from '@/components/RbModal'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import FileUploadSettingModal from './FileUploadSettingModal'
import type { Application } from '@/views/ApplicationManagement/types';
import type { Capability } from '@/views/ModelManagement/types'

interface FeaturesConfigModalProps {
  refresh: (value: FeaturesConfigForm) => void;
  source?: Application['type'];
  capability?: Capability[];
}
const max_file_count = 1;
/**
 * Modal for copying applications
 */
const FeaturesConfigModal = forwardRef<FeaturesConfigModalRef, FeaturesConfigModalProps>(({
  refresh,
  source,
  capability,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FeaturesConfigForm>();
  const values = Form.useWatch([], form)
  const fileUploadSettingModalRef = useRef<any>(null)

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  /** Open modal */
  const handleOpen = (initValue: FeaturesConfigForm) => {
    setVisible(true);
    form.setFieldsValue(initValue)
  };
  /** Copy application with new name */
  const handleSave = () => {
    setVisible(false);
    refresh(form.getFieldsValue())
  }

  const handleOpenSettings = () => {
    fileUploadSettingModalRef.current?.handleOpen(values?.file_upload)
  }

  const handleSaveSettings = (settings: FeaturesConfigForm['file_upload']) => {
    form.setFieldValue('file_upload', { ...settings, enabled: values?.file_upload?.enabled ?? false })
  }

  const formatFileTypeOptions = (fu: FeaturesConfigForm['file_upload']) => {
    let options = [{ type: 'document', enabled: fu.document_enabled, maxSize: fu.document_max_size_mb }]
    if (!capability) return options
    
    if (capability.includes('vision')) {
      options.push({ type: 'image', enabled: fu.image_enabled, maxSize: fu.image_max_size_mb })
    }
    if (capability.includes('audio')) {
      options.push({ type: 'audio', enabled: fu.audio_enabled, maxSize: fu.audio_max_size_mb })
    }
    if (capability.includes('video')) {
      options.push({ type: 'video', enabled: fu.video_enabled, maxSize: fu.video_max_size_mb })
    }
    return options.filter(item => item.enabled)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <>
      <RbModal
        title={t('application.features')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.confirm')}
        onOk={handleSave}
      >
        <Form
          form={form}
          layout="vertical"
        >
          <Flex vertical gap={12}>
            {source !== 'workflow' && <>
              <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
                <SwitchFormItem
                  title={t(`memoryConversation.web_search`)}
                  name={['web_search', "enabled"]}
                />
              </div>

              <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
                <SwitchFormItem
                  title={t('application.text_to_speech')}
                  name={['text_to_speech', "enabled"]}
                  desc={t('application.text_to_speech_desc')}
                />
              </div>
            </>}

            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t('application.file_upload')}
                name={['file_upload', "enabled"]}
                desc={values?.file_upload?.enabled ? undefined : t('application.file_upload_desc')}
              />
              {values?.file_upload?.enabled && (() => {
                const fu = values.file_upload
                // 'vision' | 'audio' | 'video'
                const filterTypes = formatFileTypeOptions(fu)
                console.log('filterTypes', filterTypes)
                return filterTypes.length > 0 ? <>
                  <Flex gap={12} className="rb:py-2!">
                    <div className="rb:flex-1 rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:bg-white rb:text-[12px]">
                      <div className="rb:grid rb:grid-cols-2 rb:gap-2 rb:text-[12px] rb:text-[#5B6167] rb:border-b rb:border-b-[#DFE4ED]">
                        <div className="rb:px-3 rb:py-1">{t(`application.supportedTypes`)}</div>
                        <div className="rb:px-3 rb:py-1">{t('application.singleMaxSize')}</div>
                      </div>
                      {filterTypes.map((item, index) => (
                        <div key={item.type} className={clsx('rb:grid rb:grid-cols-2 rb:gap-2', {
                          'rb:border-b rb:border-b-[#DFE4ED]': index !== filterTypes.length - 1
                        })}>
                          <div className="rb:px-3 rb:py-1">{t(`application.${item.type}`)}</div>
                          <div className="rb:px-3 rb:py-1">{item.maxSize} MB</div>
                        </div>
                      ))}
                    </div>
                    <div>
                      <div className="rb:text-[12px] rb:text-[#5B6167] rb:py-1">{t('application.maxCount')}</div>
                      {max_file_count} {t('application.unix')}
                    </div>
                  </Flex>
                  <Button block onClick={handleOpenSettings}>{t('application.setting')}</Button>
                </> : <Button block onClick={handleOpenSettings}>{t('application.setting')}</Button>
              })()}
              <Form.Item name="file_upload" hidden />
            </div>
          </Flex>
        </Form>
      </RbModal>

      <FileUploadSettingModal
        ref={fileUploadSettingModalRef}
        onSave={handleSaveSettings}
        capability={capability}
      />
    </>
  );
});

export default FeaturesConfigModal;