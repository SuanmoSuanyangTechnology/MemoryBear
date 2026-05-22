/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-20 14:27:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-20 14:27:10 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form } from 'antd';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import RbModal from '@/components/RbModal';
import RbSlider from '@/views/Workflow/components/Properties/RbSlider';
import ModelSelect from '@/components/ModelSelect';
import { updateAnnotationsSettings, getAnnotationsSettings } from '@/api/application';
import type { AnnotationSettingForm, AnnotationSettingModalRef } from '../types';

const AnnotationsSettingsModal = forwardRef<AnnotationSettingModalRef, { refresh: () => void }>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<AnnotationSettingForm>();
  const [initSettings, setInitSettings] = useState<AnnotationSettingForm>()

  const handleClose = () => {
    form.resetFields();
    setLoading(false);
    setVisible(false);
  };

  const handleOpen = () => {
    if (!id) {
      return;
    }
    getAnnotationsSettings(id).then(res => {
      form.setFieldsValue({
        ...res as AnnotationSettingForm,
        enabled: 1
      })
      setVisible(true);
      setInitSettings(res as AnnotationSettingForm)
    })
  };

  const handleSave = () => {
    if (!id) {
      return;
    }
    form.validateFields().then(async (values) => {
      setLoading(true);
      updateAnnotationsSettings(id, values)
        .then(() => {
          handleClose();
          refresh();
        })
        .catch(() => {
          setLoading(false);
        })
    })
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.annotationsInitialSettings')}
      open={visible}
      onCancel={handleClose}
      okText={initSettings?.enabled === 1 ? t('common.save') : t('application.saveAndEnable')}
      onOk={handleSave}
      confirmLoading={loading}
      width={480}
    >
      <Form form={form} layout="vertical">
        <Form.Item hidden name="enabled" />
        {/* Score Threshold */}
        <Form.Item
          name="similarity_threshold"
          label={t('application.similarityThreshold')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
          // TODO: ui
          tooltip={
            <div className="rb:flex rb:justify-between rb:text-[12px]">
              <span className="rb:text-[#10B981]">0.8 · {t('application.easyMatch')}</span>
              <span className="rb:text-[#155EEF]">1.0 · {t('application.preciseMatch')}</span>
            </div>
          }
        >
          <RbSlider
            min={0.8}
            max={1.0}
            step={0.01}
          />
        </Form.Item>

        {/* Embedding Model */}
        <Form.Item
          name="model_config_id"
          label={t('application.embeddingModel')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <ModelSelect
            params={{ type: 'embedding' }}
            placeholder={t('application.modelSettings')}
            className="rb:w-full"
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default AnnotationsSettingsModal;
