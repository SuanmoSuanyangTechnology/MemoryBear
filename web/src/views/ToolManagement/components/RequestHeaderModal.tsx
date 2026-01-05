import { forwardRef, useImperativeHandle, useState, type SetStateAction, type Dispatch } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { RequestHeader, RequestHeaderModalRef } from './McpServiceModal'
import RbModal from '@/components/RbModal'

const FormItem = Form.Item;

interface RequestHeaderModalProps {
  refreshTable: Dispatch<SetStateAction<RequestHeader[]>>;
}

const RequestHeaderModal = forwardRef<RequestHeaderModalRef, RequestHeaderModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<RequestHeader>();
  const [loading, setLoading] = useState(false)
  const [editIndex, setEditIndex] = useState<number | undefined>(-1)

  const values = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (index?: number, data?: RequestHeader) => {
    if (data) {
      setEditIndex(index)
      form.setFieldsValue(data)
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        if (typeof editIndex === 'number' && editIndex > -1) {
          refreshTable(prev => {
            const newList = [...prev]
            newList[editIndex] = values
            return newList
          })
        } else {
          refreshTable(prev => [...prev, values])
        }
        handleClose()
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={typeof editIndex === 'number' ? t('tool.editRequestHeader') : t('tool.addRequestHeader')}
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
        {/* 请求头名称 */}
        <FormItem
          name="key"
          label={t('tool.requestHeaderName')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter')} />
        </FormItem>
        {/* 请求头值 */}
        <FormItem
          name="value"
          label={t('tool.requestHeaderValue')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enter',)} />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default RequestHeaderModal;