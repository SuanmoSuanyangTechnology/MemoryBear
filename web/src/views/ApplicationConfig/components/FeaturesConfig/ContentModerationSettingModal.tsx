/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-29 17:47:58 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-29 17:47:58 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Radio, Switch, App } from 'antd';
import { useTranslation } from 'react-i18next';

import RbModal from '@/components/RbModal';
import type { ContentModerationConfig } from '../../types';

export interface ContentModerationSettingModalRef {
  handleOpen: (values?: ContentModerationConfig) => void;
  handleClose: () => void;
}

interface ContentModerationSettingModalProps {
  onSave: (values: ContentModerationConfig) => void;
}

const ContentModerationSettingModal = forwardRef<ContentModerationSettingModalRef, ContentModerationSettingModalProps>(({
  onSave,
}, ref) => {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<ContentModerationConfig>();

  const handleClose = (isSave?: boolean) => {
    setVisible(false);
    form.resetFields();
    if (isSave) { return; }
    onSave({
      type: 'keywords',
      enabled: false,
      config: {
        inputs_config: {
          enabled: false,
        },
        outputs_config: {
          enabled: false,
        },
      }
    })
  };

  const handleOpen = (values?: ContentModerationConfig) => {
    setVisible(true);
    if (values) {
      form.setFieldsValue({
        ...values,
        enabled: false
      });
    } else {
      form.resetFields();
    }
  };

  const handleSave = async () => {
    form.validateFields().then(values => {
      const { config } = values;
      
      if (!config?.inputs_config?.enabled && !config?.outputs_config?.enabled) {
        modal.warning({
          title: t('application.moderation_input_output_required'),
          content: t('application.moderation_input_output_required_desc'),
        });
        return;
      }

      onSave({
        ...values,
        enabled: true
      });
      handleClose(true);
    });
  };

  const values = Form.useWatch([], form);

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
  }));

  return (
    <RbModal
      title={t('application.sensitive_word_avoidance_settings')}
      open={visible}
      onCancel={() => handleClose(false)}
      onOk={handleSave}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="type"
          label={t('application.category')}
        >
          <Radio.Group>
            <Radio value="keywords">{t('application.keywords')}</Radio>
            {/* <Radio value="api">API {t('application.extension')}</Radio> */}
          </Radio.Group>
        </Form.Item>
        <Form.Item name="enabled" hidden />

        {values?.type === 'keywords' && (
          <Form.Item
            label={t('application.keywords')}
            tooltip={t('application.keywords_tips')}
            name={['config', 'keywords']}
          >
            <Input.TextArea
              placeholder={t('application.keywords_placeholder')}
              className="rb:resize-none"
            />
          </Form.Item>
        )}

        {values?.type === 'api' && (<>
          <Form.Item
            name={['config', 'api_name']}
            label={t('application.api_name')}
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item
            name={['config', 'api_endpoint']}
            label={t('application.api_endpoint')}
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item
            name={['config', 'api_key']}
            label={t('application.api_key')}
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
        </>)}

        <div className="rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-4 rb:mt-4">
          <Form.Item
            name={['config', 'inputs_config', 'enabled']}
            label={t('application.review_input_content')}
            layout="horizontal"
            valuePropName="checked"
            className="rb:mb-0!"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name={['config', 'inputs_config', 'preset_response']}
            label={t('application.preset_reply')}
            tooltip={t('application.support_markdown')}
            hidden={!values?.config?.inputs_config?.enabled}
          >
            <Input.TextArea
              placeholder={t('application.preset_reply_placeholder')}
              maxLength={100}
              className="rb:resize-none"
            />
          </Form.Item>
        </div>
        <div className="rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-4 rb:mt-4">
          <Form.Item
            name={['config', 'outputs_config', 'enabled']}
            label={t('application.review_output_content')}
            layout="horizontal"
            valuePropName="checked"
            className="rb:mb-0!"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name={['config', 'outputs_config', 'preset_response']}
            label={t('application.preset_reply')}
            tooltip={t('application.support_markdown')}
            hidden={!values?.config?.outputs_config?.enabled}
          >
            <Input.TextArea
              placeholder={t('application.preset_reply_placeholder')}
              maxLength={100}
              className="rb:resize-none"
            />
          </Form.Item>
        </div>
      </Form>
    </RbModal>
  );
});

export default ContentModerationSettingModal;
