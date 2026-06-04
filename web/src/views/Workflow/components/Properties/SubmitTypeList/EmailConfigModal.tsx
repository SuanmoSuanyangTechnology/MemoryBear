import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Flex, Input, Switch } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';

const FormItem = Form.Item;

export interface EmailConfig {
  subject: string;
  body: string;
  recipients: string;
  sendToAllMembers: boolean;
  debugMode: boolean;
}

export interface EmailConfigModalRef {
  handleOpen: (config?: EmailConfig) => void;
  handleClose: () => void;
}

interface EmailConfigModalProps {
  onSave: (config: EmailConfig) => void;
}

const EmailConfigModal = forwardRef<EmailConfigModalRef, EmailConfigModalProps>(({
  onSave
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<EmailConfig>();
  const [loading, setLoading] = useState(false);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
  };

  const handleOpen = (config?: EmailConfig) => {
    setVisible(true);
    if (config) {
      form.setFieldsValue(config);
    } else {
      form.resetFields();
    }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      onSave(values);
      handleClose();
    });
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('workflow.config.human-intervention.emailConfig.title')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
      width={520}
      cancelText={t('common.cancel')}
    >
      <div className="rb:text-[12px] rb:text-[#8F959E] rb:mb-6">{t('workflow.config.human-intervention.emailConfig.desc')}</div>

      <Form
        form={form}
        layout="vertical"
        size="middle"
        scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
      >
        <FormItem
          name="subject"
          label={t('workflow.config.human-intervention.emailConfig.subject')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input 
            placeholder={t('workflow.config.human-intervention.emailConfig.subjectPlaceholder')}
            className="rb:h-8!"
          />
        </FormItem>

        <FormItem
          name="body"
          label={t('workflow.config.human-intervention.emailConfig.body')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <div className="rb:h-32 rb:w-full rb:border rb:border-[#E5E6EB] rb:rounded-md rb:p-3 rb:bg-[#F9FAFB] rb:text-[12px]">
            <span className="rb:text-[#1677FF] rb:bg-[#E8F4FD] rb:px-1.5 rb:py-0.5 rb:rounded">{t('workflow.config.human-intervention.emailConfig.requestUrl')}</span>
          </div>
        </FormItem>

        <FormItem
          name="recipients"
          label={t('workflow.config.human-intervention.emailConfig.recipients')}
        >
          <Flex gap={8} className="rb:mb-2">
            <span className="rb:text-[12px]">{t('workflow.config.human-intervention.emailConfig.addWorkspaceMember')}</span>
            <button className="rb:text-[12px] rb:text-[#1677FF] rb:ml-auto flex items-center gap-1">
              {t('common.select')}
            </button>
          </Flex>
          <Input 
            placeholder={t('workflow.config.human-intervention.emailConfig.recipientsPlaceholder')}
            className="rb:h-8!"
          />
        </FormItem>

        <FormItem name="sendToAllMembers" valuePropName="checked">
          <Flex justify="space-between" align="center" className="rb:py-2">
            <Flex gap={8} align="center">
              <span className="rb:text-[12px]">{t('workflow.config.human-intervention.emailConfig.sendToAllMembers')}</span>
            </Flex>
            <Switch 
              className="rb:bg-[#D9D9D9]"
              checkedChildren=""
              unCheckedChildren=""
            />
          </Flex>
        </FormItem>

        <FormItem name="debugMode" valuePropName="checked">
          <Flex justify="space-between" align="center" className="rb:py-2">
            <div className="rb:flex-1">
              <Flex gap={8} align="center" className="rb:mb-1">
                <span className="rb:text-[12px]">{t('workflow.config.human-intervention.emailConfig.debugMode')}</span>
              </Flex>
              <div className="rb:text-[11px] rb:text-[#8F959E] rb:pl-12">
                {t('workflow.config.human-intervention.emailConfig.debugModeDesc')}
              </div>
            </div>
            <Switch 
              className="rb:bg-[#D9D9D9]"
              checkedChildren=""
              unCheckedChildren=""
            />
          </Flex>
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default EmailConfigModal;
