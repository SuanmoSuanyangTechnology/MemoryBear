/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-20 14:26:54 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-20 14:26:54 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Button, Checkbox, Input, message } from 'antd';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';

import RbModal from '@/components/RbModal';
import { createAnnotations, editAnnotations } from '@/api/application';
import type { AnnotationFormModalRef, AnnotationForm, AnnotationItem } from '../types'

const AnnotationFormModal = forwardRef<AnnotationFormModalRef, { refresh: () => void }>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [addNext, setAddNext] = useState(false);
  const [form] = Form.useForm<AnnotationForm>();
  const [editVo, setEditVo] = useState<AnnotationItem | null>(null);

  const handleClose = () => {
    form.resetFields();
    setLoading(false);
    setVisible(false);
    setAddNext(false);
    setEditVo(null)
  };

  const handleOpen = (vo?: AnnotationItem) => {
    if (vo) {
      form.setFieldsValue(vo);
      setEditVo(vo);
    }
    setVisible(true);
  };

  const handleSave = () => {
    if (!id) return;
    form.validateFields().then(async (values) => {
      setLoading(true);
      const req = editVo?.id ? editAnnotations(id, editVo?.id, values) : createAnnotations(id, values);

      req
        .then(() => {
          message.success(t('common.operateSuccess'));
          refresh();

          if (addNext) {
            form.resetFields();
          } else {
            handleClose();
          }
        })
        .finally(() => {
          setLoading(false);
        })
    })
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const footerOperations = [
    <Checkbox
      key="addNext"
      checked={addNext}
      onChange={(e) => setAddNext(e.target.checked)}
    >
      {t('application.addNextAnnotation')}
    </Checkbox>,
    <Button
      key="cancel"
      onClick={handleClose}
    >
      {t('common.cancel')}
    </Button>,
    <Button
      key="save"
      type="primary"
      onClick={handleSave}
      loading={loading}
    >
      {editVo?.id ? t('common.edit') : t('common.add')}
    </Button>
  ]

  return (
    <RbModal
      title={editVo?.id ? t('application.editAnnotations') : t('application.addAnnotations')}
      open={visible}
      onCancel={handleClose}
      footer={editVo?.id ? footerOperations.slice(1, 3) : footerOperations}
      width={480}
    >
      <Form form={form} layout="vertical">
        {/* Question */}
        <Form.Item
          name="question"
          label={t('application.question')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input.TextArea
            placeholder={t('common.pleaseEnter')}
            rows={6}
          />
        </Form.Item>

        {/* Answer */}
        <Form.Item
          name="answer"
          label={t('application.answer')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input.TextArea
            placeholder={t('common.pleaseEnter')}
            rows={6}
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default AnnotationFormModal;
