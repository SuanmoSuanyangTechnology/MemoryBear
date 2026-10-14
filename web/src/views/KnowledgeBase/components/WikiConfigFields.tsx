import type { ReactNode } from 'react';
import { Form, Input, Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import { MAX_WIKI_LANGUAGE_LENGTH, MAX_WIKI_PROMPT_LENGTH } from '@/api/wiki';

const KNOWLEDGE_WIKI_PATH = ['parser_config', 'wiki'];

interface Props {
  namePrefix?: string[];
  children?: ReactNode;
}

/** Shared fields for knowledge creation/editing and the direct Wiki settings drawer. */
export default function WikiConfigFields({ namePrefix = KNOWLEDGE_WIKI_PATH, children }: Props) {
  const { t } = useTranslation();
  const field = (name: string) => [...namePrefix, name];
  return <>
    <Form.Item name={field('enabled')} label={t('knowledgeBase.wiki.enable')} valuePropName="checked"
      dependencies={['llm_id']} rules={[({ getFieldValue }) => ({
        validator: (_, enabled) => enabled && !getFieldValue('llm_id')
          ? Promise.reject(new Error(t('knowledgeBase.wiki.modelRequired'))) : Promise.resolve(),
      })]}>
      <Switch />
    </Form.Item>
    <p className="rb:text-[#727981] rb:text-[13px] rb:leading-6 rb:mb-5 rb:-mt-2">{t('knowledgeBase.wiki.enableHelp')}</p>
    {children ?? <p className="rb:text-[#727981] rb:text-[13px] rb:leading-6 rb:mb-5">{t('knowledgeBase.wiki.modelHelp')}</p>}
    <Form.Item name={field('language')} label={t('knowledgeBase.wiki.language')}
      rules={[{ required: true, whitespace: true, message: t('knowledgeBase.wiki.languageRequired') }]}>
      <Input placeholder="zh-CN" maxLength={MAX_WIKI_LANGUAGE_LENGTH} />
    </Form.Item>
    <Form.Item name={field('extract_prompt')} label={t('knowledgeBase.wiki.extractPrompt')} extra={t('knowledgeBase.wiki.promptHelp')}>
      <Input.TextArea rows={5} maxLength={MAX_WIKI_PROMPT_LENGTH} showCount placeholder={t('knowledgeBase.wiki.promptPlaceholder')} />
    </Form.Item>
  </>;
}
