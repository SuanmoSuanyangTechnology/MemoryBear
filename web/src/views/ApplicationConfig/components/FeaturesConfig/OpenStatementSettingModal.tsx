/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-05 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 14:12:11
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Button, Form, Input, Flex, App } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import type { FeaturesConfigForm } from '../../types'
import type { Variable } from '../VariableList/types'
import Tag from '@/components/Tag'

export interface OpenStatementSettingModalRef {
  handleOpen: (values?: FeaturesConfigForm['opening_statement']) => void;
  handleClose: () => void;
}

interface OpenStatementSettingModalProps {
  onSave: (values: FeaturesConfigForm['opening_statement']) => void;
  chatVariables?: Variable[];
}

const OpenStatementSettingModal = forwardRef<OpenStatementSettingModalRef, OpenStatementSettingModalProps>(({
  onSave,
  chatVariables = []
}, ref) => {
  const { t } = useTranslation();
  const { modal } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<FeaturesConfigForm['opening_statement']>();

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
  };

  const handleOpen = (values?: FeaturesConfigForm['opening_statement']) => {
    setVisible(true);
    form.setFieldsValue(values || {});
  };

  const handleSave = async () => {
    form.validateFields().then(values => {
      if (values?.enabled && values?.statement && values?.statement?.trim() !== '') {
        const usedVars = [...new Set([...values.statement?.matchAll(/\{\{(\w+)\}\}/g)].map(m => m[1]))]

        const validNames = new Set(chatVariables.map(v => v.name))
        const invalid = usedVars.filter(v => !validNames.has(v))
        console.log('invalid', invalid)
        if (invalid.length > 0) {
          modal.confirm({
            title: t('application.invalidVariablesTitle'),
            content: invalid.map((vo, index) => <Tag key={index}>{'{{'}{vo}{'}}'}</Tag>),
            okText: t('common.confirm'),
            cancelText: t('common.cancel'),
            onOk: () => {
              onSave(values);
              handleClose();
            },
          })
        } else {
          onSave(values);
          handleClose();
        }
      }
    });
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t('application.settings')}
      open={visible}
      onCancel={handleClose}
      onOk={handleSave}
    >
      <Form form={form} layout="vertical">
        <Form.Item name="enabled" hidden />
        <Form.Item
          label={t('application.opening_statement')}
          name="statement"
        >
          <Input.TextArea
            placeholder={t('common.pleaseEnter')}
          />  
        </Form.Item>

        <Form.List name="suggested_questions">
          {(fields, { add, remove }) => (
            <Form.Item label={t('application.suggested_questions')}>
              <Flex vertical gap={4}>
              {fields.map((field, index) => (
                <Flex key={field.key} align="center" justify="space-between" gap={4}>
                  <Form.Item name={field.name} noStyle>
                    <Input
                      placeholder={t('common.pleaseEnter')}
                    />
                  </Form.Item>
                  <div
                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                    onClick={() => remove(index)}
                  ></div>
                </Flex>
              ))}
              <Button type="dashed" block onClick={() => add()}>
                + {t('common.addOption')}
              </Button>
              </Flex>
            </Form.Item>
          )}
        </Form.List>

      </Form>
    </RbModal>
  );
});

export default OpenStatementSettingModal;
