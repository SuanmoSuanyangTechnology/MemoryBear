import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { AddClassItem, OntologyClassModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { createOntologyClass } from '@/api/ontology'

const FormItem = Form.Item;

interface OntologyClassModalProps {
  refresh: () => void;
}

const OntologyClassModal = forwardRef<OntologyClassModalRef, OntologyClassModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<AddClassItem>();
  const [loading, setLoading] = useState(false)
  const [scene_id, setSceneId] = useState<string | null>(null)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (scene_id: string) => {
    form.resetFields();
    setVisible(true);
    setSceneId(scene_id)
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    if (!scene_id) return;
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        createOntologyClass({
          scene_id: scene_id,
          classes: [{ ...values }]
        }).then(() => {
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

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('ontology.addClass')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="class_name"
          label={t('ontology.class_name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>

        <FormItem
          name="class_description"
          label={t('ontology.class_description')}
        >
          <Input.TextArea placeholder={t('ontology.classDescriptionPlaceholder')} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default OntologyClassModal;