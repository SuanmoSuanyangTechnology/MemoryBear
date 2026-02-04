import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { OntologyItem, OntologyModalData, OntologyModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { createOntologyScene, updateOntologyScene } from '@/api/ontology'

const FormItem = Form.Item;

interface OntologyModalProps {
  refresh: () => void;
}

const OntologyModal = forwardRef<OntologyModalRef, OntologyModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [editVo, setEditVo] = useState<OntologyItem | null>(null)
  const [form] = Form.useForm<OntologyModalData>();
  const [loading, setLoading] = useState(false)

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditVo(null)
  };

  const handleOpen = (vo?: OntologyItem) => {
    if (vo) {
      setEditVo(vo);
      form.setFieldsValue(vo);
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        const request = editVo?.scene_id ? updateOntologyScene(editVo.scene_id, values) : createOntologyScene(values)
        request
          .then(() => {
            message.success(t('common.saveSuccess'));
            handleClose();
            refresh();
          })
          .finally(() => setLoading(false))
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={editVo?.scene_id ? t('ontology.edit') : t('ontology.create')}
      open={visible}
      onCancel={handleClose}
      okText={editVo?.scene_id ? t('common.save') : t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="scene_name"
          label={t('ontology.scene_name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="scene_description"
          label={t('ontology.scene_description')}
        >
          <Input.TextArea placeholder={t('ontology.descriptionPlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyModal;