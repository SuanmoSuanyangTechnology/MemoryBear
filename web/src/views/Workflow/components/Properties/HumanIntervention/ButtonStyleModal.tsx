import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Flex, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';

const FormItem = Form.Item;

export interface ButtonStyleModalRef {
  handleOpen: (value?: string, buttonText?: string) => void;
  handleClose: () => void;
}

interface ButtonStyleModalProps {
  onSave: (variant: string) => void;
}

const buttonStyles = [
  {
    value: 'primary',
    className: 'rb:bg-[#1677FF] rb:text-white rb:border-none',
    label: 'primary'
  },
  {
    value: 'default',
    className: 'rb:bg-white rb:text-[#1D2129] rb:border rb:border-[#E5E6EB]',
    label: 'default'
  },
  {
    value: 'link',
    className: 'rb:bg-transparent rb:text-[#1677FF] rb:border-none',
    label: 'link'
  },
  {
    value: 'text',
    className: 'rb:bg-transparent rb:text-[#1D2129] rb:border-none',
    label: 'text'
  }
];

const ButtonStyleModal = forwardRef<ButtonStyleModalRef, ButtonStyleModalProps>(({
  onSave
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ variant: string }>();
  const [loading, setLoading] = useState(false);
  const [buttonText, setButtonText] = useState('Button Text');

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setButtonText('Button Text');
  };

  const handleOpen = (value?: string, text?: string) => {
    setVisible(true);
    if (value) {
      form.setFieldsValue({ variant: value });
    } else {
      form.resetFields();
    }
    if (text) {
      setButtonText(text);
    }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      onSave(values.variant);
      handleClose();
    });
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const currentStyle = Form.useWatch(['variant'], form);

  return (
    <RbModal
      title={t('workflow.config.human-intervention.selectButtonStyle')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
      width={400}
      cancelText={t('common.cancel')}
    >
      <Form
        form={form}
        layout="vertical"
        size="middle"
      >
        <FormItem
          name="variant"
          label={t('workflow.config.human-intervention.buttonStyle')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <Flex gap={16} wrap="wrap">
            {buttonStyles.map((variant) => (
              <div
                key={variant.value}
                onClick={() => form.setFieldsValue({ variant: variant.value })}
                className={`rb:cursor-pointer rb:p-4 rb:border rb:rounded-lg rb:flex rb:items-center rb:justify-center rb:w-[calc(50%-8px)] rb:h-16 ${
                  currentStyle === variant.value
                    ? 'rb:border-[#1677FF] rb:bg-[#E8F4FD]'
                    : 'rb:border-[#E5E6EB] rb:bg-white'
                }`}
              >
                <Button
                  type={variant.value as any}
                  className={`${variant.className} rb:min-w-25`}
                >
                  {buttonText}
                </Button>
              </div>
            ))}
          </Flex>
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ButtonStyleModal;
