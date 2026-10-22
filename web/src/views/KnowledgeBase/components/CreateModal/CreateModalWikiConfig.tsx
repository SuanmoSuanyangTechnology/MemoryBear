import { Form, Input, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import SwitchFormItem from '@/components/FormItem/SwitchFormItem';

const { TextArea } = Input;

const CreateModalWikiConfig = () => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const enabled = Form.useWatch(['parser_config', 'wiki', 'enabled'], form) || false;

  return (
    <>
      <SwitchFormItem
        name={['parser_config', 'wiki', 'enabled']}
        title={t('knowledgeBase.wikiConfig.enabled')}
        desc={t('knowledgeBase.wikiConfig.enabledTips')}
        className="rb:w-full rb:p-4! rb:border rb:border-[#EBEBEB] rb:rounded-lg rb:mb-4!"
      />

      <Form.Item
        name={['parser_config', 'wiki', 'language']}
        label={t('knowledgeBase.wikiConfig.language')}
        rules={[{ required: true, message: t('common.pleaseSelect') + t('knowledgeBase.wikiConfig.language') }]}
        hidden={true}
      >
        <Select options={[{ value: 'zh-CN', label: t('knowledgeBase.wikiConfig.chinese') }]} />
      </Form.Item>
      <Form.Item
        name={['parser_config', 'wiki', 'extract_prompt']}
        label={t('knowledgeBase.wikiConfig.extractPrompt')}
        extra={t('knowledgeBase.wikiConfig.extractPromptTips')}
        hidden={!enabled}
      >
        <TextArea rows={6} placeholder={t('knowledgeBase.wikiConfig.extractPromptPlaceholder')} />
      </Form.Item>
    </>
  );
};

export default CreateModalWikiConfig;
