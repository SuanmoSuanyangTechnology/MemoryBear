/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-29 17:48:38
 */
/**
 * Copy Application Modal
 * Allows users to duplicate an existing application with a new name
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Button, Flex, Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { FeaturesConfigModalRef, FeaturesConfigForm } from '../../types'
import RbModal from '@/components/RbModal'
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import FileUploadSettingModal from './FileUploadSettingModal'
import type { Application } from '@/views/ApplicationManagement/types';
import type { Capability } from '@/views/ModelManagement/types'
import OpenStatementSettingModal, { type OpenStatementSettingModalRef } from './OpenStatementSettingModal'
import ContentModerationSettingModal, { type ContentModerationSettingModalRef } from './ContentModerationSettingModal'
import type { ContentModerationConfig } from '../../types'
import type { Variable } from '../VariableList/types'
import LabelWrapper from '@/components/FormItem/LabelWrapper'

interface FeaturesConfigModalProps {
  refresh: (value: FeaturesConfigForm) => void;
  source?: Application['type'];
  capability?: Capability[];
  chatVariables: Variable[];
}
const max_file_count = 1;
/**
 * Modal for copying applications
 */
const FeaturesConfigModal = forwardRef<FeaturesConfigModalRef, FeaturesConfigModalProps>(({
  refresh,
  source,
  capability,
  chatVariables
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FeaturesConfigForm>();
  const values = Form.useWatch([], form)
  const fileUploadSettingModalRef = useRef<any>(null)
  const openStatementSettingModalRef = useRef<OpenStatementSettingModalRef>(null)
  const contentModerationSettingModalRef = useRef<ContentModerationSettingModalRef>(null)

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
    form.validateFields().then((values) => {
      console.log('feature handleSave values', values)
      setVisible(false);
      refresh(values)
    })
  }

  const handleOpenSettings = () => {
    fileUploadSettingModalRef.current?.handleOpen(values?.file_upload)
  }

  const handleSaveSettings = (settings: FeaturesConfigForm['file_upload']) => {
    form.setFieldValue('file_upload', { ...settings, enabled: values?.file_upload?.enabled ?? false })
  }

  const formatFileTypeOptions = (fu: FeaturesConfigForm['file_upload']) => {
    let options = fu.document_enabled ? [{ type: 'document', enabled: fu.document_enabled, maxSize: fu.document_max_size_mb }] : []
    if (!capability && source !== 'workflow') return options
    
    if ((capability?.includes('vision') || source === 'workflow') && fu.image_enabled) {
      options.push({ type: 'image', enabled: fu.image_enabled, maxSize: fu.image_max_size_mb })
    }
    if ((capability?.includes('audio') || source === 'workflow') && fu.audio_enabled) {
      options.push({ type: 'audio', enabled: fu.audio_enabled, maxSize: fu.audio_max_size_mb })
    }
    if ((capability?.includes('video') || source === 'workflow') && fu.video_enabled) {
      options.push({ type: 'video', enabled: fu.video_enabled, maxSize: fu.video_max_size_mb })
    }
    return options.filter(item => item.enabled)
  }

  const handleOpenStatementSettings = () => {
    openStatementSettingModalRef.current?.handleOpen(values?.opening_statement)
  }
  const handleSaveStatement = (settings: FeaturesConfigForm['opening_statement']) => {
    form.setFieldValue('opening_statement', settings)
  }

  const handleOpenContentModerationSettings = () => {
    contentModerationSettingModalRef.current?.handleOpen(values?.sensitive_word_avoidance ?? { type: 'keywords' })
  }

  const handleSaveContentModeration = (settings: ContentModerationConfig) => {
    console.log('handleSaveContentModeration settings', settings)
    form.setFieldValue('sensitive_word_avoidance', { ...settings })
  }
  const handleSwitchContentModeration = (checked: boolean) => {
    if (checked) {
      handleOpenContentModerationSettings()
    } else {
      form.setFieldValue('sensitive_word_avoidance', { ...values?.sensitive_word_avoidance, enabled: false })
    }
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
            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t('application.opening_statement')}
                name={['opening_statement', "enabled"]}
                desc={values?.opening_statement?.enabled ? undefined : t('application.opening_statement_desc')}
              />
              {values?.opening_statement?.enabled && (() => {
                const statement = values.opening_statement?.statement
                return statement && statement.trim() !== '' ? <>
                  <div className="rb:bg-white rb:rounded-lg rb:py-1 rb:px-3 rb:mb-1">
                    {statement}
                  </div>
                  <Button block onClick={handleOpenStatementSettings}>{t('application.editOpeningStatement')}</Button>
                </> : <Button block onClick={handleOpenStatementSettings}>{t('application.editOpeningStatement')}</Button>
              })()}
              <Form.Item name="opening_statement" hidden />
            </div>
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
              {/* suggested_questions_after_answer */}
              <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
                <SwitchFormItem
                  title={t('application.suggested_questions_after_answer')}
                  name={['suggested_questions_after_answer', "enabled"]}
                />
              </div>
            </>}
            <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
              <SwitchFormItem
                title={t(`application.citation`)}
                name={['citation', "enabled"]}
                desc={t('application.citation_desc')}
              />
              <SwitchFormItem
                title={t(`application.allow_download`)}
                name={['citation', "allow_download"]}
                disabled={!values?.citation?.enabled}
                className="rb:mt-2!"
              />
            </div>
            {source === 'workflow' && <>
              <div className="rb:relative rb:border rb:border-[#DFE4ED] rb:p-3 rb:rounded-lg rb:bg-[#f5f7fc]">
                <Flex
                  align="center"
                  justify="space-between"
                >
                  {/* Label and description section */}
                  <LabelWrapper title={t('application.sensitive_word_avoidance')}>
                    {values?.sensitive_word_avoidance?.enabled ? undefined : t('application.sensitive_word_avoidance_desc')}
                  </LabelWrapper>

                  <Switch value={values?.sensitive_word_avoidance?.enabled} onChange={handleSwitchContentModeration} />
                </Flex>
                <Form.Item name="sensitive_word_avoidance" hidden />

              {values?.sensitive_word_avoidance?.enabled && <>
                  <Flex gap={12} className="rb:py-2!">
                    <div className="rb:flex-1 rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:bg-white rb:text-[12px]">
                      <div className="rb:grid rb:grid-cols-2 rb:gap-2 rb:text-[12px] rb:text-[#5B6167] rb:border-b rb:border-b-[#DFE4ED]">
                        <div className="rb:px-3 rb:py-1">{t(`application.category`)}</div>
                        <div className="rb:px-3 rb:py-1">{t('application.singleMaxSize')}</div>
                      </div>
                      <div className={clsx('rb:grid rb:grid-cols-2 rb:gap-2')}>
                        <div className="rb:px-3 rb:py-1">{t(`application.${values?.sensitive_word_avoidance?.type}` || '')}</div>
                        <div className="rb:px-3 rb:py-1">
                          {[
                            values?.sensitive_word_avoidance?.config?.inputs_config?.enabled ? t('application.review_input_content') : null,
                            values?.sensitive_word_avoidance?.config?.outputs_config?.enabled ? t('application.review_output_content') : null,
                          ].filter(Boolean).join(' | ')}
                        </div>
                      </div>
                    </div>
                  </Flex>
                <Button block onClick={() => handleSwitchContentModeration(true)}>{t('application.setting')}</Button>
              </>}
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
        source={source}
      />
      <OpenStatementSettingModal
        ref={openStatementSettingModalRef}
        source={source}
        chatVariables={chatVariables}
        onSave={handleSaveStatement}
      />
      <ContentModerationSettingModal
        ref={contentModerationSettingModalRef}
        onSave={handleSaveContentModeration}
      />
    </>
  );
});

export default FeaturesConfigModal;