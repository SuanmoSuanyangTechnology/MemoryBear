import { useCallback, useState } from 'react';
import { Alert, App, Button, Space, Table, Tag, Tooltip } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { generateDocumentWiki, getWikiDocuments, WIKI_POLL_INTERVAL } from '@/api/wiki';
import type { KnowledgeBaseDocumentData } from '../types';
import { documentState } from './documentState';
import { useWikiResource, wikiErrorMessage } from './useWikiResource';

interface Props {
  kbId: string; query: string; enabled: boolean; onDocument: (id: string) => void; onImport: () => void; onManage: () => void;
}

export default function WikiFiles({ kbId, query, enabled, onDocument, onImport, onManage }: Props) {
  const [page, setPage] = useState(1);
  const [submitting, setSubmitting] = useState<string>();
  const [submission, setSubmission] = useState<{ name: string; taskId: string }>();
  const [submitError, setSubmitError] = useState<string>();
  const { message } = App.useApp();
  const load = useCallback((signal: AbortSignal) => getWikiDocuments(kbId, page, query, signal), [kbId, page, query]);
  const { data, loading, error, reload } = useWikiResource(load, WIKI_POLL_INTERVAL);

  const generate = async (document: KnowledgeBaseDocumentData) => {
    if (!document.id) return;
    setSubmitting(document.id);
    setSubmitError(undefined);
    setSubmission(undefined);
    try {
      const result = await generateDocumentWiki(kbId, document.id);
      setSubmission({ name: document.file_name || '文档', taskId: result.task_id });
      message.success('Wiki 任务已受理，处理状态将自动刷新。');
    } catch (cause) {
      setSubmitError(wikiErrorMessage(cause));
    } finally {
      setSubmitting(undefined);
      reload();
    }
  };

  const columns: ColumnsType<KnowledgeBaseDocumentData> = [
    { title: '文件名称', dataIndex: 'file_name', key: 'name', render: (name: string, document) =>
      <button className="wiki-text-link wiki-file-name" onClick={() => document.id && onDocument(document.id)}>{name}</button> },
    { title: '处理状态', width: 128, key: 'status', render: (_, document) => {
      const status = documentState(document);
      return <Tag color={status.color}>{status.label}</Tag>;
    } },
    { title: '处理进度', dataIndex: 'progress_msg', key: 'progress', render: (value: string) => {
      const text = value?.replace(/\\n/g, '\n') || '—';
      return <Tooltip title={<pre className="wiki-progress-tooltip">{text}</pre>}><div className="wiki-progress-message">{text}</div></Tooltip>;
    } },
    { title: '操作', width: 150, key: 'action', render: (_, document) => {
      const failed = (document.progress ?? 0) < 0 || document.run === 2;
      const retryRetraction = document.status === 0 && failed;
      return <Button size="small" disabled={(!enabled || document.status === 0) && !retryRetraction || !document.id || !!submitting}
        loading={submitting === document.id} onClick={() => void generate(document)}>
        {retryRetraction ? '重试来源更新' : failed ? '重试 Wiki' : '生成 Wiki'}
      </Button>;
    } },
  ];

  return <section className="wiki-files-view">
    <div className="wiki-files-heading"><div><h1>文件</h1><p>查看源文档、生成进度，或为文档重新生成 Wiki。</p></div>
      <Space wrap><Button onClick={onManage}>文件管理</Button><Button icon={<ReloadOutlined />} onClick={reload} aria-label="刷新文件" />
        <Button type="primary" icon={<PlusOutlined />} onClick={onImport}>导入文件</Button></Space>
    </div>
    {!enabled ? <Alert type="info" showIcon message="Wiki 自动生成已关闭。已有页面仍可阅读，开启后可继续生成。" /> : null}
    {submission ? <Alert closable onClose={() => setSubmission(undefined)} type="success" showIcon
      message={`${submission.name}：任务已受理`} description={`任务 ID：${submission.taskId}`} /> : null}
    {submitError ? <Alert closable onClose={() => setSubmitError(undefined)} type="error" showIcon message="Wiki 任务提交失败"
      description={submitError} /> : null}
    {error ? <Alert type="error" showIcon message="文件列表加载失败" description={error} action={<Button onClick={reload}>重试</Button>} />
      : <Table rowKey="id" columns={columns} dataSource={data?.items ?? []} loading={loading}
        scroll={{ x: 720 }} locale={{ emptyText: query ? '没有匹配的文件' : '暂无源文档，请先导入文件' }}
        pagination={{ current: page, pageSize: 20, total: data?.page.total ?? 0, showSizeChanger: false, onChange: setPage }} />}
  </section>;
}
