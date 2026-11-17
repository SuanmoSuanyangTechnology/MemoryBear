import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Alert, App, Button, Drawer, Dropdown, List, Progress, Radio, Space, Typography, Upload } from 'antd';
import { getFaqImport, submitFaqImport } from '@/api/faqImport';
import type { ImportItem, ImportMode, ImportProgress } from '@/api/faqImport';
import { downloadFaqTemplate, parseFaqFile } from './importFiles';

export interface FaqImportRef { open: () => void }
interface Props { knowledgeBaseId: string; total: number; refresh: () => void }
const stageNames = { queued: '等待处理', validating: '正在校验', indexing: '正在准备索引', writing: '正在写入', finished: '处理完成' };
const FaqImport = forwardRef<FaqImportRef, Props>(({ knowledgeBaseId, total, refresh }, ref) => {
  const { modal, message, notification } = App.useApp();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<ImportMode>('append');
  const [busy, setBusy] = useState(false);
  const [filename, setFilename] = useState('');
  const [errors, setErrors] = useState<{ position: number; question: string; reason: string }[]>([]);
  const [fileError, setFileError] = useState('');
  const [progress, setProgress] = useState<ImportProgress | null>(null);
  const [pollError, setPollError] = useState(false);
  const storageKey = `faq-import:${knowledgeBaseId}`;
  const [taskId, setTaskId] = useState<string | null>(() => localStorage.getItem(storageKey));
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;
  useImperativeHandle(ref, () => ({ open: () => {
    if (taskId || busy) { message.info('当前知识库正在导入，请等待完成'); return; }
    setOpen(true); setMode('append'); setErrors([]); setFileError(''); setFilename('');
  } }));
  useEffect(() => { setTaskId(localStorage.getItem(storageKey)); setProgress(null); setPollError(false); }, [storageKey]);
  useEffect(() => {
    if (!taskId || pollError) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await getFaqImport(knowledgeBaseId, taskId);
        if (cancelled) return;
        setProgress(result);
        if (result.status === 'pending' || result.status === 'running') { timer = setTimeout(poll, 2000); return; }
        localStorage.removeItem(storageKey); setTaskId(null); refreshRef.current();
        const summary = result.summary;
        const success = result.status === 'succeeded';
        notification.open({
          key: `faq-result:${taskId}`, placement: 'topRight', duration: success ? 3 : 0,
          type: success ? 'success' : result.status === 'partially_succeeded' ? 'warning' : 'error',
          message: success ? '导入成功' : result.status === 'partially_succeeded' ? '导入完成，部分失败' : '导入失败',
          description: <div>
            <p>共 {summary?.total_count ?? result.progress.total_count} 条，成功 {summary?.success_count ?? 0} 条，失败 {summary?.failure_count ?? 0} 条</p>
            {result.failure && <p>{result.failure.message}</p>}
            {!!result.failed_entries?.length && <div style={{ maxHeight: 320, overflowY: 'auto' }}>
              {result.failed_entries.map(item => <p key={item.item_index}>第 {item.source_position} 行 / 条目 · {item.standard_question || '问题未填写'}<br />{item.reason}</p>)}
            </div>}
          </div>,
        });
      } catch { if (!cancelled) setPollError(true); }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [knowledgeBaseId, taskId, storageKey, notification, pollError]);

  const submit = async (items: ImportItem[], selectedMode: ImportMode) => {
    setBusy(true);
    try {
      const result = await submitFaqImport(knowledgeBaseId, selectedMode, items);
      localStorage.setItem(storageKey, result.task_id); setTaskId(result.task_id); setPollError(false); setOpen(false);
    } catch (e) {
      const err = e as { response?: { status?: number } };
      setFileError(err.response?.status === 409 ? '当前知识库已有导入任务，请等待完成后再试。' : '提交未完成，请核对列表和任务状态后再操作，避免重复提交。');
    } finally { setBusy(false); }
  };
  const choose = async (file: File) => {
    setBusy(true); setFilename(file.name); setErrors([]); setFileError('');
    try {
      const items = await parseFaqFile(file);
      if (!items.length) throw new Error('文件中没有问答条目');
      const invalid = items.filter(item => !item.standard_question || !item.standard_answer).map(item => ({ position: item.source_position, question: item.standard_question, reason: !item.standard_question && !item.standard_answer ? '问题和答案均不能为空' : !item.standard_question ? '问题不能为空' : '答案不能为空' }));
      if (invalid.length) { setErrors(invalid); return; }
      if (mode === 'replace') {
        modal.confirm({ title: '全量覆盖当前知识库的 FAQ？', okText: '确认全量覆盖', cancelText: '返回检查', okButtonProps: { danger: true },
          content: `将用本次成功导入的问答替换当前全部 ${total} 条 FAQ，包含所有来源、审核和启用状态。本次文件共 ${items.length} 条；如果全部失败则保留原 FAQ。文件与 Wiki 不受影响。`,
          onOk: () => submit(items, 'replace'),
        });
      } else await submit(items, 'append');
    } catch (e) { setFileError(e instanceof Error ? e.message : '文件无法解析'); }
    finally { setBusy(false); }
  };
  return <>
    {taskId && <div style={{ padding: '12px 16px', background: 'var(--rb-color-bg-container, #fafafa)', borderRadius: 8 }}>
      {pollError ? <Alert type="warning" showIcon message="进度暂时无法读取，任务可能仍在执行" action={<Button size="small" onClick={() => setPollError(false)}>继续查询</Button>} /> : <>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}><Typography.Text>{stageNames[progress?.stage ?? 'queued']}</Typography.Text><Typography.Text>{progress?.progress.processed_count ?? 0} / {progress?.progress.total_count ?? '—'}</Typography.Text></Space>
        <Progress percent={progress ? Math.round(progress.progress.processed_count / progress.progress.total_count * 100) : 0} status="active" />
      </>}
    </div>}
    <Drawer title="批量导入 FAQ" open={open} width={560} onClose={() => { if (!busy) setOpen(false); }} footer={<Button onClick={() => setOpen(false)} disabled={busy}>取消</Button>}>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Typography.Text strong>导入模式 *</Typography.Text>
        <Radio.Group value={mode} onChange={e => setMode(e.target.value)} disabled={busy}>
          <Space direction="vertical"><Radio value="append">追加导入 — 保留现有 FAQ，新增成功条目</Radio><Radio value="replace">全量覆盖 — 用本次成功条目替换全部 FAQ</Radio></Space>
        </Radio.Group>
        <Space style={{ width: '100%', justifyContent: 'space-between' }}><Typography.Text strong>上传文件 *</Typography.Text>
          <Dropdown menu={{ items: ['csv', 'xlsx', 'json'].map(key => ({ key, label: key.toUpperCase() + ' 示例' })), onClick: ({ key }) => { void downloadFaqTemplate(key as 'csv' | 'xlsx' | 'json'); } }}><Button type="link">下载示例文件</Button></Dropdown>
        </Space>
        <Upload.Dragger accept=".csv,.xlsx,.json" multiple={false} disabled={busy} showUploadList={false} beforeUpload={file => { void choose(file); return false; }}>
          <p>{busy ? '正在解析或提交…' : filename || '点击上传或拖拽文件到此处'}</p><p>选择一个文件</p>
        </Upload.Dragger>
        <Typography.Text type="secondary">支持 CSV、XLSX、JSON。问题、答案均为必填；Excel 读取第一个工作表。CSV / Excel 表头：问题、答案；JSON 字段：question、answer。检查通过后开始导入。</Typography.Text>
        {fileError && <Alert type="error" showIcon message={fileError} />}
        {!!errors.length && <><Alert type="error" showIcon message={`发现 ${errors.length} 条必填项问题，请修正文件后重新选择`} /><List pagination={{ pageSize: 5, size: 'small' }} dataSource={errors} renderItem={item => <List.Item><div><strong>{item.question || '问题未填写'}</strong><p>原文件第 {item.position} 行 / 条目：{item.reason}</p></div></List.Item>} /></>}
      </Space>
    </Drawer>
  </>;
});
export default FaqImport;
