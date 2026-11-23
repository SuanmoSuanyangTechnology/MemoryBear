/**
 * Local FAQ import flow mirroring the detail-page prototype:
 * parse -> auto submit on success -> short polling -> result toast.
 * On page entry it also restores an in-flight task from the current-task API.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { App } from 'antd';

import { getCurrentFaqImportTask, getFaqImportProgress, importFaqs } from '@/api/kbWiki';
import {
  fileImportError,
  isFaqImportReady,
  parseFaqImportFile,
  toFaqImportItems,
  type FaqImportParseResult,
} from '../faqImportFile';
import { dismissFaqImportResult, showFaqImportResult } from '../FaqImportStatus';
import type { FaqImportFailedEntryVo, FaqImportMode, FaqImportTaskVo } from '../types';
import useFaqImportI18n from './useFaqImportI18n';

const POLL_INTERVAL_MS = 800;

export type FaqImportStage = 'input' | 'submitting' | 'running' | 'done';

interface UseFaqImportOptions {
  knowledgeBaseId: string;
  onCompleted?: () => void;
  /** 现有 FAQ 总数，用于全量覆盖时的二次确认文案。 */
  existingCount?: number;
}

const isTerminal = (status: FaqImportTaskVo['status']) =>
  status === 'succeeded' || status === 'partially_succeeded' || status === 'failed';

export default function useFaqImport({ knowledgeBaseId, onCompleted, existingCount = 0 }: UseFaqImportOptions) {
  const { message, notification, modal } = App.useApp();
  const key = useFaqImportI18n();

  const [open, setOpen] = useState(false);
  const [mode, setModeState] = useState<FaqImportMode>('append');
  const [fileName, setFileName] = useState('');
  const [readId, setReadId] = useState(0);
  const [parsing, setParsing] = useState(false);
  const [parseResult, setParseResult] = useState<FaqImportParseResult | null>(null);
  const [stage, setStage] = useState<FaqImportStage>('input');
  const [task, setTask] = useState<FaqImportTaskVo | null>(null);

  const sessionRef = useRef(0);
  const readVersionRef = useRef(0);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const completedRef = useRef(onCompleted);
  const keyRef = useRef(key);
  const modeRef = useRef(mode);
  const stageRef = useRef(stage);
  const isJsonRef = useRef(false);
  const existingCountRef = useRef(existingCount);
  completedRef.current = onCompleted;
  keyRef.current = key;
  modeRef.current = mode;
  stageRef.current = stage;
  existingCountRef.current = existingCount;

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const dismissNotice = useCallback(() => {
    dismissFaqImportResult(notification);
  }, [notification]);

  const showNotice = useCallback((title: string, summary: string, errors: string[], failed: boolean) => {
    showFaqImportResult(notification, { title, summary, errors, failed });
  }, [notification]);

  const formatEntry = useCallback((entry: FaqImportFailedEntryVo) => {
    const position = keyRef.current(isJsonRef.current ? 'positionItem' : 'positionLine', {
      line: entry.source_position,
    });
    return `${position}：${entry.reason}`;
  }, []);

  const finishFaqImport = useCallback((vo: FaqImportTaskVo) => {
    stopPolling();
    setStage('done');
    setTask(vo);
    completedRef.current?.();

    const t = keyRef.current;
    const summary = vo.summary;
    const total = summary?.total_count ?? vo.progress.total_count;
    const success = summary?.success_count ?? 0;
    const failure = summary?.failure_count ?? 0;
    const entries = (vo.failed_entries ?? []).map(formatEntry);
    const taskFailure = vo.failure?.message ? [vo.failure.message] : [];

    if (vo.status === 'succeeded') {
      showNotice(t('success'), t('summarySuccess', { total, success }), [], false);
      return;
    }
    showNotice(
      vo.status === 'partially_succeeded' ? t('resultPartialTitle') : t('failed'),
      t('summaryPartial', { total, success, failure }),
      [...entries, ...taskFailure],
      true,
    );
  }, [formatEntry, showNotice, stopPolling]);

  const startPolling = useCallback((taskId: string) => {
    stopPolling();
    const session = sessionRef.current;
    const poll = async () => {
      if (session !== sessionRef.current) return;
      try {
        const vo = await getFaqImportProgress(knowledgeBaseId, taskId);
        if (session !== sessionRef.current) return;
        setTask(vo);
        if (isTerminal(vo.status)) {
          finishFaqImport(vo);
          return;
        }
      } catch {
        if (session !== sessionRef.current) return;
        stopPolling();
        setStage('done');
        const t = keyRef.current;
        showNotice(t('pollFailedTitle'), t('pollFailedSummary'), [t('pollFailedEntry')], true);
        return;
      }
      pollTimerRef.current = setTimeout(() => { void poll(); }, POLL_INTERVAL_MS);
    };
    void poll();
  }, [knowledgeBaseId, finishFaqImport, showNotice, stopPolling]);

  const commitImport = useCallback((result: FaqImportParseResult, selectedMode: FaqImportMode) => {
    const session = sessionRef.current;
    setStage('submitting');
    dismissNotice();
    importFaqs(knowledgeBaseId, { mode: selectedMode, items: toFaqImportItems(result) })
      .then((response) => {
        if (session !== sessionRef.current) return;
        setOpen(false);
        setTask(null);
        setStage('running');
        startPolling(response.task_id);
      })
      .catch(() => {
        if (session !== sessionRef.current) return;
        // The global request interceptor already surfaces the error; no result toast here.
        setOpen(false);
        setStage('done');
        setTask(null);
      });
  }, [knowledgeBaseId, startPolling, dismissNotice]);

  const readFile = useCallback((file: File) => {
    if (parsing || stage === 'submitting' || stage === 'running') return;
    const version = ++readVersionRef.current;
    const session = sessionRef.current;
    isJsonRef.current = /\.json$/i.test(file.name);
    setFileName(file.name);
    setReadId((id) => id + 1);
    setParsing(true);
    setParseResult(null);
    setStage('input');
    dismissNotice();
    parseFaqImportFile(file)
      .then((result) => {
        if (version !== readVersionRef.current || session !== sessionRef.current) return;
        // Validation alone never imports; the user must click the confirm button.
        setParseResult(result);
      })
      .catch(() => {
        if (version !== readVersionRef.current || session !== sessionRef.current) return;
        setParseResult(fileImportError('readFailed'));
      })
      .finally(() => {
        if (version !== readVersionRef.current || session !== sessionRef.current) return;
        setParsing(false);
      });
  }, [parsing, stage, dismissNotice]);

  // Explicit confirm: append commits directly; replace needs a second confirmation.
  const confirmImport = useCallback(() => {
    if (stage !== 'input' || !parseResult || !isFaqImportReady(parseResult)) return;
    const selectedMode = modeRef.current;
    if (selectedMode !== 'replace') {
      commitImport(parseResult, selectedMode);
      return;
    }
    const t = keyRef.current;
    modal.confirm({
      title: t('replaceConfirmTitle'),
      content: t('replaceConfirmDesc', {
        importCount: parseResult.rows.length,
        totalCount: existingCountRef.current,
      }),
      okText: t('replaceConfirmOk'),
      cancelText: t('replaceBack'),
      okButtonProps: { danger: true },
      onOk: () => {
        commitImport(parseResult, selectedMode);
      },
    });
  }, [stage, parseResult, commitImport, modal]);

  const openImport = useCallback(() => {
    if (stage === 'submitting' || stage === 'running') {
      message.warning(key('taskBusy'));
      return;
    }
    sessionRef.current += 1;
    readVersionRef.current += 1;
    stopPolling();
    setModeState('append');
    setFileName('');
    setReadId(0);
    setParsing(false);
    setParseResult(null);
    setStage('input');
    setTask(null);
    dismissNotice();
    setOpen(true);
  }, [stage, message, key, stopPolling, dismissNotice]);

  const closeImport = useCallback(() => setOpen(false), []);

  const setMode = useCallback((next: FaqImportMode) => {
    if (parsing || stage === 'submitting' || stage === 'running') return;
    setModeState(next);
  }, [parsing, stage]);

  // On page entry, restore an in-flight task and keep its progress/status in sync.
  useEffect(() => {
    let cancelled = false;
    getCurrentFaqImportTask(knowledgeBaseId)
      .then((response) => {
        if (cancelled) return;
        // A locally started task takes precedence over the restored one.
        if (stageRef.current === 'submitting' || stageRef.current === 'running') return;
        if (!response.task_id) return;
        setOpen(false);
        setTask(null);
        setStage('running');
        startPolling(response.task_id);
      })
    return () => { cancelled = true; };
  }, [knowledgeBaseId, startPolling]);

  useEffect(() => () => {
    sessionRef.current += 1;
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
  }, []);

  return {
    open,
    mode,
    setMode,
    fileName,
    readId,
    parsing,
    parseResult,
    stage,
    task,
    openImport,
    closeImport,
    readFile,
    confirmImport,
    dismissNotice,
  };
}
