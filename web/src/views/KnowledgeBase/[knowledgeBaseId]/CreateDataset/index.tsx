import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, Button, Flex, Form, Modal, Steps } from 'antd';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import type { UploadFilesRef } from '@/components/Upload/UploadFiles';
import type { TableRef } from '@/components/Table';
import {
  createDocumentAndUpload,
  deleteDocument,
  knowledgesChunkPolicy,
  parseDocument,
  updateDocument,
} from '@/api/knowledgeBase';
import type { KnowledgeBaseDocumentData, ParserConfig } from '@/views/KnowledgeBase/types';
import exitIcon from '@/assets/images/knowledgeBase/exit.png';
import { parentChildBlockConfigValues } from './ParentChildBlockConfig';
import SourceStep from './SourceStep';
import ParameterStep from './ParameterStep';
import ConfirmStep from './ConfirmStep';
import useDatasetUpload from './hooks/useDatasetUpload';
import useDocumentPolling from './hooks/useDocumentPolling';
import {
  stepIndexMap,
  type CreateDatasetFormValues,
  type CreateDatasetLocationState,
  type SourceType,
} from './types';
import '../Private.css';
import type { UploadFile } from 'antd';

const defaultValues: CreateDatasetFormValues = {
  pdfEnhancementEnabled: true,
  pdfEnhancementMethod: 'mineru',
  processingMethod: 'directBlock',
  parameterSettings: 'defaultSettings',
  blockSize: 512,
  chunkOverlap: 52,
  qaPrompt: undefined,
  ...parentChildBlockConfigValues,
  image: {
    vision_enabled: true,
    vision_mode: '1',
  },
};

const CreateDataset = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { modal, message: messageApi } = App.useApp();
  const { knowledgeBaseId: routeKnowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const locationState = (location.state ?? {}) as CreateDatasetLocationState;
  const source = (locationState.source ?? 'local') as SourceType;
  const knowledgeBaseId = locationState.knowledgeBaseId || routeKnowledgeBaseId;
  const parentId = locationState.parentId;
  const initialIds = locationState.fileIds || locationState.fileId;
  const [current, setCurrent] = useState(stepIndexMap[locationState.startStep ?? 'selectFile']);
  const [fileIds, setFileIds] = useState<string[]>(initialIds ? (Array.isArray(initialIds) ? initialIds : [initialIds]) : []);
  const [isParentChildMode, setIsParentChildMode] = useState<boolean | null>(null);
  const [form] = Form.useForm<CreateDatasetFormValues>();
  const tableRef = useRef<TableRef>(null);
  const uploadRef = useRef<UploadFilesRef>(null);
  const { title, content } = Form.useWatch(['title', 'content'], form) || {};

  const handleBack = useCallback(() => {
    if (!knowledgeBaseId) return;
    navigate(`/knowledge-base/${knowledgeBaseId}/private`, {
      state: {
        refresh: true,
        timestamp: Date.now(),
        navigateToDocumentFolder: parentId !== knowledgeBaseId ? parentId : undefined,
      },
    });
  }, [knowledgeBaseId, navigate, parentId]);

  const { loading: pollingLoading, poll, start: startPolling } = useDocumentPolling({
    knowledgeBaseId,
    fileIds,
    tableRef,
    onCompleted: handleBack,
  });

  const addFileId = useCallback((id: string) => {
    setFileIds((previous) => previous.includes(id) ? previous : [...previous, id]);
  }, []);
  const removeFileId = useCallback((id: string) => {
    setFileIds((previous) => previous.filter((item) => item !== id));
  }, []);
  const { upload, remove } = useDatasetUpload({
    source,
    knowledgeBaseId,
    parentId,
    messageApi,
    t,
    onUploaded: addFileId,
    onCsvUploaded: handleBack,
    onRemoved: removeFileId,
  });

  useEffect(() => {
    if (!knowledgeBaseId) return;
    void knowledgesChunkPolicy(knowledgeBaseId).then((response) => {
      const parentChildMode = (response as { parent_child_mode: boolean }).parent_child_mode;
      setIsParentChildMode(parentChildMode);
      form.setFieldValue('processingMethod', parentChildMode ? 'parentChildBlock' : 'directBlock');
    });
  }, [form, knowledgeBaseId]);

  const steps = useMemo(() => [
    { title: t('knowledgeBase.selectFile') },
    { title: t('knowledgeBase.parameterSettings') },
    { title: t('knowledgeBase.confirmUpload') },
  ], [t]);

  const requireUploadedFile = () => {
    if (fileIds.length > 0) return true;
    Modal.warning({
      title: t('common.warning') || 'Warning',
      content: t('knowledgeBase.pleaseUploadFileFirst') || 'Please upload files first',
    });
    return false;
  };

  const saveTextDocument = async () => {
    const values = await form.validateFields(['title', 'content']);
    const response = await createDocumentAndUpload(
      { title: values.title, content: values.content },
      { kb_id: knowledgeBaseId, parent_id: parentId },
    ) as { id?: string };
    if (response.id) setFileIds([response.id]);
  };

  const buildParserConfig = (values: CreateDatasetFormValues): ParserConfig & { chunk_overlap?: number; qa_prompt?: string } => {
    const config: ParserConfig & { chunk_overlap?: number; qa_prompt?: string } = {
      layout_recognize: values.pdfEnhancementEnabled ? values.pdfEnhancementMethod : undefined,
      delimiter: values.delimiter,
      chunk_token_num: values.blockSize,
      chunk_overlap: values.processingMethod === 'directBlock' ? values.chunkOverlap : undefined,
      auto_questions: values.processingMethod === 'qaExtract' ? 1 : 0,
      qa_prompt: values.qaPrompt,
      image: values.image,
    };
    if (values.processingMethod === 'parentChildBlock') {
      Object.assign(config, {
        parent_chunk_mode: values.parent_chunk_mode,
        parent_chunk_delimiter: values.parent_chunk_delimiter,
        parent_chunk_token_num: values.parent_chunk_token_num,
        chunk_token_num: values.chunk_token_num,
      });
    }
    return config;
  };

  const saveParserSettings = async () => {
    const values = await form.validateFields();
    console.log('values', values)
    if (
      values.processingMethod === 'directBlock' &&
      (!Number.isInteger(values.chunkOverlap) || values.chunkOverlap <= 0 || values.chunkOverlap >= values.blockSize)
    ) {
      messageApi.warning(t('knowledgeBase.chunkOverlapRange'));
      return false;
    }
    const parserConfig = buildParserConfig(values);
    await Promise.all(fileIds.map((id) => updateDocument(id, { progress: 0, parser_config: parserConfig })));
    await poll(false);
    return true;
  };

  const handleNext = async () => {
    if (current === 0) {
      if (source === 'csv') return;
      try {
        if (source === 'text') await saveTextDocument();
        else if (source === 'local' && !requireUploadedFile()) return;
      } catch {
        messageApi.error(t('knowledgeBase.createContentError'));
        return;
      }
    }
    if (current === 1) {
      if (!requireUploadedFile() || !(await saveParserSettings())) return;
    }
    setCurrent((value) => Math.min(value + 1, 2));
  };

  const handleStartUpload = () => {
    if (!requireUploadedFile()) return;
    modal.confirm({
      title: t('knowledgeBase.startUploadConfirmTitle') || 'Start processing documents',
      content: t('knowledgeBase.startUploadConfirmContent'),
      okText: t('knowledgeBase.returnToList'),
      cancelText: t('knowledgeBase.stayOnPage'),
      onOk: () => {
        fileIds.forEach((id) => void parseDocument(id, {}));
        handleBack();
      },
      onCancel: () => {
        fileIds.forEach((id) => void parseDocument(id, {}));
        startPolling();
      },
    });
  };

  const handleDelete = (record: KnowledgeBaseDocumentData) => {
    if (!record.id) return;
    modal.confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: record.file_name }),
      onOk: async () => {
        await deleteDocument(record.id as string);
        removeFileId(record.id as string);
        messageApi.success(t('common.deleteSuccess'));
        tableRef.current?.loadData();
      },
    });
  };

  const textFormValid = Boolean(title?.trim() && content?.trim());

  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const onFileListChange = (fileList: UploadFile[]) => {
    setFileList(fileList);
  };
  console.log('fileList', fileList);

  return (
    <Form form={form} initialValues={defaultValues} layout="vertical" component={false}>
      <Flex vertical className="rb:p-3! rb:pt-2! rb:h-full">
        <Flex align="center" gap={8} className="rb:mb-4! rb:cursor-pointer" onClick={handleBack}>
          <img src={exitIcon} alt="exit" className="rb:w-4 rb:h-4" />
          <span className="rb:text-gray-500 rb:text-sm">{t('common.exit')}</span>
        </Flex>
        {source !== 'csv' && <div className="rb:px-24 rb:py-5 rb:bg-white rb:rounded-xl"><Steps current={current} items={steps} className="custom-steps" /></div>}
        <div className="rb:bg-white rb:rounded-xl rb:flex-1 rb:mt-3">
          {current === 0 && <SourceStep source={source} uploadRef={uploadRef} onUpload={upload} onRemove={remove} fileList={fileList} onFileListChange={onFileListChange} />}
          {current === 1 && <ParameterStep form={form} fileIds={fileIds} isParentChildMode={isParentChildMode} />}
          {current === 2 && <ConfirmStep tableRef={tableRef} knowledgeBaseId={knowledgeBaseId} fileIds={fileIds} onDelete={handleDelete} />}
          <Flex gap={12} className={`rb:p-6! rb:mt-6! ${current === 1 || ((source === 'link' || source === 'text') && current === 0) ? 'rb:pl-28! rb:mt-10!' : ''}`}>
            {current !== 0 && <Button onClick={() => setCurrent((value) => Math.max(value - 1, 0))} disabled={pollingLoading}>{t('common.previous') || 'Prev'}</Button>}
            {source !== 'csv' && (
              <Button
                type="primary"
                onClick={current === 2 ? handleStartUpload : handleNext}
                disabled={pollingLoading || (current === 0 && source === 'local' && fileIds.length === 0) || (current === 0 && source === 'text' && !textFormValid)}
              >
                {current === 2 ? t('knowledgeBase.startUploading') : t('common.next')}
              </Button>
            )}
          </Flex>
        </div>
      </Flex>
    </Form>
  );
};

export default CreateDataset;
