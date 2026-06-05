import { forwardRef, useImperativeHandle, useState, useEffect, useRef } from 'react';
import { Form, Select, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SubmitTypeItem, SubmitTypeEditModalRef } from './types'
import type { EmailConfig, EmailConfigModalRef } from './EmailConfigModal'
import RbModal from '@/components/RbModal'
import EmailConfigModal from './EmailConfigModal'

const FormItem = Form.Item;

interface SubmitTypeEditModalProps {
  refresh: (type: string) => void;
}

const submitTypes = [
  {
    type: 'webapp',
    icon: 'rb:bg-[#1677FF] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'W',
  },
  {
    type: 'email',
    icon: 'rb:bg-[#722ED1] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
    iconText: 'E',
    hasConfig: true,
    disabled: true
  },
  // {
  //   type: 'slack',
  //   icon: 'rb:bg-[#E01E5A] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
  //   iconText: 'S',
  //   disabled: true
  // },
  // {
  //   type: 'teams',
  //   icon: 'rb:bg-[#464EB8] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
  //   iconText: 'T',
  //   disabled: true
  // },
  // {
  //   type: 'discord',
  //   icon: 'rb:bg-[#5865F2] rb:rounded-md rb:size-4 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-[10px]',
  //   iconText: 'D',
  //   disabled: true
  // }
]

const SubmitTypeEditModal = forwardRef<SubmitTypeEditModalRef, SubmitTypeEditModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SubmitTypeItem>();
  const [loading, setLoading] = useState(false)
  const [editIndex, setEditIndex] = useState<number | undefined>(undefined)
  const [currentConfig, setCurrentConfig] = useState<EmailConfig | undefined>(undefined)
  
  const emailConfigModalRef = useRef<EmailConfigModalRef>(null)
  const firstRenderRef = useRef(true)

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setEditIndex(undefined)
    setCurrentConfig(undefined)
    firstRenderRef.current = true
  };

  const handleOpen = (variable?: SubmitTypeItem) => {
    setVisible(true);
    // if (variable) {
    //   form.setFieldsValue(variable)
    //   if (variable.type === 'email' && variable.description) {
    //     try {
    //       setCurrentConfig(JSON.parse(variable.description))
    //     } catch {
    //       setCurrentConfig(undefined)
    //     }
    //   }
    //   setEditIndex(index)
    // } else {
      form.resetFields();
      setEditIndex(undefined)
      setCurrentConfig(undefined)
    // }
  };

  const handleSave = () => {
    form.validateFields().then((values) => {
      refresh(values?.type)
      handleClose()
    })
  }

  const handleEmailConfigSave = (config: EmailConfig) => {
    setCurrentConfig(config)
    handleSave()
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const currentType = Form.useWatch(['type'], form);
  const selectedType = submitTypes.find(item => item.type === currentType);

  // 如果选择了需要配置的类型，直接保存并关闭当前弹窗
  useEffect(() => {
    if (visible && selectedType?.hasConfig && !firstRenderRef.current) {
      emailConfigModalRef.current?.handleOpen(currentConfig)
    }
  }, [selectedType, visible, currentConfig]);

  return (
    <>
      <RbModal
        title={editIndex !== undefined ? t('workflow.config.human-intervention.editSubmitType') : t('workflow.config.human-intervention.addSubmitType')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.save')}
        onOk={handleSave}
        confirmLoading={loading}
        width={520}
        cancelText={t('common.cancel')}
      >
        <Form
          form={form}
          layout="vertical"
          size="middle"
          scrollToFirstError={{ behavior: 'instant', block: 'end', focus: true }}
        >
          <FormItem
            name="type"
            label={t('workflow.config.human-intervention.submitType')}
            rules={[{ required: true, message: t('common.pleaseSelect') }]}
          >
            <Select
              placeholder={t('common.pleaseSelect')}
              options={submitTypes.map(item => ({
                value: item.type,
                label: (
                  <Flex align="center" gap={8} className={item.disabled ? 'rb:opacity-65' : ''}>
                    <div className={item.icon}>{item.iconText}</div>
                    <span>{t(`workflow.config.human-intervention.submitTypes.${item.type}`)}</span>
                    {item.disabled && <span className="rb:text-[12px] rb:text-[#999]">COMING SOON</span>}
                  </Flex>
                ),
                disabled: item.disabled
              }))}
            />
          </FormItem>
        </Form>
      </RbModal>

      <EmailConfigModal
        ref={emailConfigModalRef}
        onSave={handleEmailConfigSave}
      />
    </>
  );
});

export default SubmitTypeEditModal;
