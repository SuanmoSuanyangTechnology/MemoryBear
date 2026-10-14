import { useEffect, useState } from 'react';
import { Alert, App, Button, Drawer, Form, Input, Select, Space, Switch } from 'antd';
import { getCustomWorkspaceModels } from '@/api/workspaces';
import { getWikiKnowledge, saveWikiConfig, type WikiConfig } from '@/api/wiki';
import type { KnowledgeBase } from '../types';
import { wikiErrorMessage } from './useWikiResource';

interface Values extends WikiConfig { llm_id?: string }
interface Props { knowledge: KnowledgeBase; open: boolean; onClose: () => void; onSaved: () => void }
interface ModelOption { id: string; name: string; is_active?: boolean }

export default function WikiSettings({ knowledge, open, onClose, onSaved }: Props) {
  const [form] = Form.useForm<Values>();
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const { message } = App.useApp();

  useEffect(() => {
    if (!open) return;
    setError(undefined);
    form.setFieldsValue({ enabled: false, language: 'zh-CN', extract_prompt: '', ...knowledge.parser_config?.wiki, llm_id: knowledge.llm_id });
    let active = true;
    setModelsLoading(true);
    getCustomWorkspaceModels().then(result => {
      if (active) setModels((result as { llm?: ModelOption[] }).llm ?? []);
    }).catch(() => {
      if (active) setError('模型列表加载失败。已有模型配置仍可使用，请关闭设置后重试。');
    }).finally(() => { if (active) setModelsLoading(false); });
    return () => { active = false; };
  }, [open, knowledge, form]);

  const save = async () => {
    const values = await form.validateFields();
    setSaving(true);
    setError(undefined);
    try {
      const { llm_id, ...wiki } = values;
      await saveWikiConfig(knowledge.id, wiki, llm_id);
      message.success('Wiki 设置已保存');
      onSaved();
      onClose();
    } catch (cause) {
      // Config may have committed even when publishing the backfill failed.
      setError(wikiErrorMessage(cause));
      try {
        const current = await getWikiKnowledge(knowledge.id);
        form.setFieldsValue({ ...current.parser_config?.wiki, llm_id: current.llm_id });
      } catch { /* Keep the form visible for an explicit retry. */ }
    } finally { setSaving(false); }
  };

  const modelOptions = models.filter(model => model.is_active !== false).map(model => ({ value: model.id, label: model.name }));
  if (knowledge.llm_id && !modelOptions.some(model => model.value === knowledge.llm_id)) {
    modelOptions.unshift({ value: knowledge.llm_id, label: `当前知识库模型（${knowledge.llm_id}）` });
  }

  return <Drawer title="Wiki 设置" open={open} onClose={saving ? undefined : onClose} width={460}
    extra={<Space><Button onClick={onClose} disabled={saving}>取消</Button><Button type="primary" loading={saving} onClick={() => void save().catch(() => undefined)}>保存</Button></Space>}>
    <Form form={form} layout="vertical">
      {error ? <Alert className="wiki-settings-alert" type="warning" showIcon message={error} /> : null}
      <Form.Item name="enabled" label="自动生成 Wiki" valuePropName="checked"><Switch /></Form.Item>
      <p className="wiki-settings-help">开启后将为已有文档补录 Wiki，并自动处理后续导入的文档。关闭后停止受理新的生成任务，已受理的任务继续完成，已有页面仍可阅读。</p>
      <Form.Item name="llm_id" label="文本生成模型" dependencies={['enabled']}
        rules={[({ getFieldValue }) => ({ validator: (_, value) => getFieldValue('enabled') && !value
          ? Promise.reject(new Error('请选择文本生成模型')) : Promise.resolve() })]}>
        <Select loading={modelsLoading} showSearch optionFilterProp="label" options={modelOptions} placeholder="选择知识库的文本生成模型" />
      </Form.Item>
      <p className="wiki-settings-help">与知识库的文本生成模型共用。修改后将用于后续文档处理。</p>
      <Form.Item name="language" label="生成语言" rules={[{ required: true, whitespace: true, message: '请输入生成语言' }]}>
        <Input placeholder="zh-CN" maxLength={64} />
      </Form.Item>
      <Form.Item name="extract_prompt" label="提取提示词" extra="留空使用默认提示词；修改后用于后续生成任务。">
        <Input.TextArea rows={7} maxLength={12000} showCount placeholder="例如：优先整理产品能力、使用方法与架构设计，保留来源与适用版本。" />
      </Form.Item>
    </Form>
  </Drawer>;
}
