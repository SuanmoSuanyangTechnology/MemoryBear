import { useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { App, Flex, Form, Progress, theme } from 'antd';
import type { FormInstance } from 'antd';
import {
  CheckCircleFilled,
  DownOutlined,
  LoadingOutlined,
  UpOutlined,
  UploadOutlined,
  WarningFilled,
} from '@ant-design/icons';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import {
  createDocumentWithFile,
  getDocumentDetailV1,
  patchDocumentConfiguration,
  rechunkDocumentV1,
  uploadAndParseDocument,
} from '@/api/knowledgeBaseDocument';
import { knowledgesChunkPolicy } from '@/api/knowledgeBase';
import { useUploadProgress, type UploadTask, type UploadTaskStatus } from '@/store/uploadProgress';
import { getFileIconClassByMimeType } from '@/utils/fileIcon';
import type { KnowledgeBaseDocumentData, KnowledgeBaseFormData } from '@/views/KnowledgeBase/types';
import type {
  DocumentProcessingWrite,
  ProcessingDispatchResult,
} from '@/views/KnowledgeBase/typesDocumentV1';
import useCreateModalModels from './useCreateModalModels';
import { fieldSectionMap } from '../CreateKnowledgeBaseModal/constants';
import {
  defaultFormValues,
  documentToFormValues,
  formValuesToProcessingConfiguration,
  isConfigSectionKey,
  processingConfigurationToFormValues,
} from './constants';
import type {
  ConfigSectionKey,
  SelectedFile,
  UploadFileModalV1Props,
  UploadFormValues,
} from './types';
import RbAlert from '@/components/RbAlert';
import Tag from '@/components/Tag'
import StatusTag from '@/components/StatusTag'

const toSelectedFiles = (files: File[]): SelectedFile[] =>
  files.map((file, index) => ({
    uid: `${Date.now()}-${index}-${file.name}`,
    name: file.name,
    size: file.size,
    originFile: file,
  }));

/** When dispatch fails or the result is unknown, prompt the user to verify instead of treating it as pure success. */
const hasDispatchIssue = (processing?: ProcessingDispatchResult): boolean =>
  processing?.status === 'dispatch_failed' || processing?.status === 'dispatch_unknown';

const UPLOAD_NOTIFICATION_KEY = 'knowledge-base-upload-progress';

/** Manages the upload modal's file list, form validation targeting, and modal state for the presentation components to consume. */
const useUploadFileModal = ({ refresh }: UploadFileModalV1Props) => {
  const { t } = useTranslation();
  const { message: messageApi, notification: notificationApi } = App.useApp();
  const { token } = theme.useToken();
  const [form] = Form.useForm<UploadFormValues>();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeSection, setActiveSection] = useState<ConfigSectionKey>('parsing');
  const [fileList, setFileList] = useState<SelectedFile[]>([]);
  const [isRechunkMode, setIsRechunkMode] = useState(false);
  const [isParentChildMode, setIsParentChildMode] = useState<boolean | null>(null);
  const contextRef = useRef<{ knowledgeBaseId: string; parentId?: string; kbName: string }>({ knowledgeBaseId: '', kbName: '' });
  const rechunkDocumentRef = useRef<string | null>(null);
  const rechunkRequestIdRef = useRef(0);
  const { addTasks, updateProgress, finishTask, dismiss, setExpanded } = useUploadProgress();

  const { customModels, getTypeList, resetModelInfo } = useCreateModalModels({
    form: form as unknown as FormInstance<KnowledgeBaseFormData>,
    datasets: null,
    visible,
  });

  const resetParsingForm = () => {
    form.resetFields();
    form.setFieldsValue(defaultFormValues);
  };

  /** Refresh the bottom-right notification with the same key to avoid producing multiple notifications for multiple files. */
  /** Refresh the bottom-right notification with the same key; tasks from multiple uploads are grouped and accumulated by knowledge base. */
  const renderUploadNotification = () => {
    const { tasks, dismissed, expanded } = useUploadProgress.getState();
    if (tasks.length === 0 || dismissed) return;

    const uploadingCount = tasks.filter((task) => task.status === 'uploading').length;
    const successCount = tasks.filter((task) => task.status === 'success' || task.status === 'dispatch_issue').length;
    const failedCount = tasks.filter((task) => task.status === 'failed').length;
    const doneCount = tasks.length - uploadingCount;
    const allDone = uploadingCount === 0;
    const hasFailure = failedCount > 0;
    const hasIssue = hasFailure || tasks.some((task) => task.status === 'dispatch_issue');
    const totalBytes = tasks.reduce((sum, task) => sum + task.total, 0);
    const loadedBytes = tasks.reduce((sum, task) => sum + task.loaded, 0);
    const overallPercent = totalBytes > 0 ? Math.min(100, Math.round((loadedBytes / totalBytes) * 100)) : 0;

    const groups = new Map<string, UploadTask[]>();
    tasks.forEach((task) => {
      const group = groups.get(task.kbName) ?? [];
      group.push(task);
      groups.set(task.kbName, group);
    });

    notificationApi.open({
      key: UPLOAD_NOTIFICATION_KEY,
      placement: 'bottomRight',
      duration: 0,
      onClose: () => dismiss(),
      message: (
        <Flex gap={12} className={`rb:p-4!${expanded ? ' rb:pb-0!' : ''}`}>
          <Tag
            circle={true}
            color={hasFailure && allDone ? 'error' : 'success'}
            variant="borderless"
            className="rb:shrink-0! rb:size-9! rb:text-center rb:text-[18px]! rb:py-1.75!"
          >
            <UploadOutlined />
          </Tag>
          <Flex vertical gap={4} className="rb:flex-1 rb:pr-3!">
            <Flex align="center" justify="space-between" gap={6} className="rb:w-full!">
              <div className="rb:font-semibold rb:truncate">
                {allDone
                  ? t('knowledgeBase.uploadModal.importCompleted', { done: doneCount, total: tasks.length })
                  : t('knowledgeBase.uploadModal.importing', { done: doneCount, total: tasks.length })}
              </div>
              <button
                type="button"
                title={t('knowledgeBase.uploadModal.togglePanel')}
                aria-label={t('knowledgeBase.uploadModal.togglePanel')}
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(!useUploadProgress.getState().expanded);
                  renderUploadNotification();
                }}
                className="rb:shrink-0 rb:size-5.5 rb:-mb-1.5! rb:cursor-pointer rb:border-none rb:bg-transparent rb:leading-none rb:text-gray-600 rb:rounded-lg rb:hover:text-gray-900 rb:hover:bg-[rgba(23,23,25,0.06)]"
              >
                {expanded ? <UpOutlined className="rb:text-xs" /> : <DownOutlined className="rb:text-xs" />}
              </button>
            </Flex>
            <div className="rb:text-xs rb:text-gray-500">
              {groups.size === 1
                ? t('knowledgeBase.uploadModal.uploadingTo', { name: tasks[0]?.kbName ?? '' })
                : t('knowledgeBase.uploadModal.uploadingToCount', { count: groups.size })}
            </div>
          </Flex>
        </Flex>
      ),
      description: expanded ? (
        <Flex vertical gap={8} className="rb:-mt-2!">
          <Flex vertical className="rb:px-4!">
            <Progress
              percent={overallPercent}
              showInfo={false}
              size="small"
              strokeColor={hasFailure && allDone ? token.colorError : token.colorSuccess}
            />

            <Flex align="center" gap={8}>
              {uploadingCount > 0 &&
                <Flex align="center" className="rb:text-[12px]!">
                  <StatusTag className="rb:border-none! rb:text-primary-600! rb:mr-0!" status="default" text={t('knowledgeBase.uploadModal.importingCount', { count: uploadingCount })} />
                  <span className="rb:font-medium">{uploadingCount}</span>
                </Flex>
              }
              {failedCount > 0 &&
                <Flex align="center" className="rb:text-[12px]!">
                  <StatusTag className="rb:border-none! rb:text-red-500!" status="error" text={t('knowledgeBase.uploadModal.importFailedCount', { count: failedCount })} />
                  <span className="rb:font-medium">{failedCount}</span>
                </Flex>
              }
              {successCount > 0 &&
                <Flex align="center" className="rb:text-[12px]!">
                  <StatusTag className="rb:border-none! rb:text-green-600!" status="success" text={t('knowledgeBase.uploadModal.importSuccessCount', { count: successCount })} />
                  <span className="rb:font-medium">{successCount}</span>
                </Flex>
              }
            </Flex>
          </Flex>
          <Flex vertical gap={8} className="rb-border-t rb:px-4! rb:py-3!">
            <RbAlert color={uploadingCount > 0 ? 'blue' : hasIssue ? 'red' : 'green'}>
              <Flex align="center" gap={6}>
                {hasIssue && allDone ? <WarningFilled /> : null}
                <span>
                  {uploadingCount > 0
                    ? t('knowledgeBase.uploadModal.importWarning')
                    : hasFailure
                      ? t('knowledgeBase.uploadModal.importFailedTip')
                      : hasIssue
                        ? t('knowledgeBase.uploadModal.importIssueTip')
                        : t('knowledgeBase.uploadModal.importSuccessTip')}
                </span>
              </Flex>
            </RbAlert>
            {Array.from(groups.entries()).map(([kbName, groupTasks]) => (
              <Flex vertical gap={8} key={kbName}>
                {groupTasks.map((task) => (
                  <Flex vertical gap={4} key={task.id}>
                    {groups.size > 1 &&
                      <span className="rb:text-xs rb:text-gray-600 rb:mb-1!">{t('knowledgeBase.uploadModal.uploadingTo', { name: kbName })}</span>
                    }
                    <Flex justify="space-between" gap={8} className="rb:py-1 rb:text-xs">
                      <Flex align="center" gap={8} className="rb:min-w-0">
                        <div
                          className={clsx(
                            'rb:size-6! rb:shrink-0 rb:bg-cover rb:opacity-65',
                            getFileIconClassByMimeType(task.mimeType, task.fileName),
                          )}
                        />
                        <div>
                          <div className="rb:truncate">{task.fileName}</div>
                          <div className="rb:mt-1 rb:text-gray-600">
                            {task.status === 'uploading'
                              ? t('knowledgeBase.uploadModal.fileImporting', {
                                  percent: task.total > 0 ? Math.min(100, Math.round((task.loaded / task.total) * 100)) : 0,
                                })
                              : task.status === 'failed'
                                ? t('knowledgeBase.uploadModal.fileFailed')
                                : t('knowledgeBase.uploadModal.fileSuccess')
                            }
                          </div>
                        </div>
                      </Flex>
                      {task.status === 'uploading'
                        ? <LoadingOutlined className="rb:text-blue-500!" />
                        : task.status === 'failed'
                          ? <WarningFilled className="rb:text-red-500!" />
                          : <CheckCircleFilled className="rb:text-green-600!" />
                      }
                    </Flex>
                  </Flex>
                ))}
              </Flex>
            ))}
          </Flex>
        </Flex>
      ) : undefined,
      className: 'rb:shadow-[0_6px_28px_#17171924,0_1px_4px_#17171912]! rb:p-0!'
    });
  };

  const updateUploadProgress = (id: string, loaded: number, total?: number) => {
    updateProgress(id, loaded, total);
    renderUploadNotification();
  };

  const finishUploadTask = (id: string, status: UploadTaskStatus) => {
    finishTask(id, status);
    renderUploadNotification();
  };

  const handleClose = () => {
    setVisible(false);
    setLoading(false);
    setActiveSection('parsing');
    setIsRechunkMode(false);
    setIsParentChildMode(null);
    setFileList([]);
    rechunkDocumentRef.current = null;
    form.resetFields();
    resetModelInfo();
  };

  /** Fetch the knowledge base chunking strategy: parent_child_mode determines the available chunking methods; when applyDefault, initialize the default chunking method accordingly. */
  const fetchChunkPolicy = (knowledgeBaseId: string, applyDefault = false) => {
    void knowledgesChunkPolicy(knowledgeBaseId)
      .then((response) => {
        const parentChildMode = (response as { parent_child_mode: boolean }).parent_child_mode;
        setIsParentChildMode(parentChildMode);
        if (applyDefault) {
          form.setFieldValue('chunk_strategy', parentChildMode ? 'parent-child' : 'default');
        }
      })
      .catch(() => setIsParentChildMode(null));
  };

  // Called after the parent selects files: show the files and open the parsing config modal.
  const handleOpen = (files: File[], knowledgeBaseId: string, parentId?: string, kbName?: string) => {
    rechunkRequestIdRef.current += 1;
    contextRef.current = { knowledgeBaseId, parentId, kbName: kbName ?? '' };
    rechunkDocumentRef.current = null;
    setIsRechunkMode(false);
    setActiveSection('parsing');
    setFileList(toSelectedFiles(files));
    resetParsingForm();
    setVisible(true);
    getTypeList();
    fetchChunkPolicy(knowledgeBaseId, true);
  };

  // Rechunking: first backfill from the list config, then override with the detail API's processing config.
  const handleRechunk = (document: KnowledgeBaseDocumentData, knowledgeBaseId: string, parentId?: string) => {
    const requestId = ++rechunkRequestIdRef.current;
    const documentId = document.id || document.file_id;
    contextRef.current = { knowledgeBaseId, parentId, kbName: '' };
    rechunkDocumentRef.current = documentId || null;
    setIsRechunkMode(true);
    setActiveSection('parsing');
    setFileList([{
      uid: documentId || 'rechunk-document',
      name: document.file_name || '',
      size: document.file_size || 0,
      originFile: new File([], document.file_name || ''),
      documentId,
    }]);
    setVisible(true);
    getTypeList();
    fetchChunkPolicy(knowledgeBaseId);

    const legacyValues = documentToFormValues(document);
    const fillForm = (configuration?: DocumentProcessingWrite | null) => {
      const values = processingConfigurationToFormValues(configuration);
      form.setFieldsValue({
        ...values,
        // Fall back to the list config's document type when the detail is not yet returned.
        document_kind: configuration ? values.document_kind : legacyValues.document_kind,
      });
    };
    form.resetFields();
    fillForm(null);
    if (documentId) {
      getDocumentDetailV1(documentId)
        .then((detail) => {
          if (requestId === rechunkRequestIdRef.current) fillForm(detail.processing_configuration);
        })
        .catch(() => undefined);
    }
  };

  // "Add files" in the modal appends after the parent selects again, without resetting the existing config.
  const handleAddFiles = (files: File[]) => {
    setFileList((prev) => [...prev, ...toSelectedFiles(files)]);
  };

  const handleRemove = (uid: string) => {
    setFileList((prev) => prev.filter((item) => item.uid !== uid));
  };

  const handleConfirm = () => {
    if (fileList.length === 0) {
      messageApi.warning(t('knowledgeBase.uploadModal.addFileFirst'));
      return;
    }

    form
      .validateFields()
      .then((values) => {
        setLoading(true);
        const processingConfiguration = formValuesToProcessingConfiguration(values);
        const { knowledgeBaseId, parentId, kbName } = contextRef.current;
        const documentId = rechunkDocumentRef.current;

        if (isRechunkMode) {
          if (!documentId) {
            messageApi.warning(t('knowledgeBase.uploadModal.rechunkMissingId'));
            return Promise.resolve();
          }
          return patchDocumentConfiguration(documentId, { processing_configuration: processingConfiguration })
            .then(() => rechunkDocumentV1(documentId))
            .then((result) => {
              if (hasDispatchIssue(result.processing)) {
                messageApi.warning(t('knowledgeBase.uploadModal.dispatchIssue'));
              } else {
                messageApi.success(t('knowledgeBase.uploadModal.rechunkSuccess'));
              }
            });
        }

        addTasks(fileList.map((item) => ({
          id: item.uid,
          fileName: item.name,
          kbName,
          mimeType: item.originFile?.type,
          status: 'uploading',
          loaded: 0,
          total: item.size || 1,
        })));
        renderUploadNotification();
        // Close the modal immediately after starting the import; progress is shown in the bottom-right notification.
        handleClose();

        return Promise
          .all(fileList.map((item) => {
            const markFailed = (error: unknown) => {
              finishUploadTask(item.uid, 'failed');
              throw error;
            };
            if (item.fileId) {
              return createDocumentWithFile({
                kb_id: knowledgeBaseId,
                file_id: item.fileId,
                processing_configuration: processingConfiguration,
              })
                .then(() => {
                  finishUploadTask(item.uid, 'success');
                  return undefined;
                })
                .catch(markFailed);
            }
            return uploadAndParseDocument(
              item.originFile,
              knowledgeBaseId,
              parentId || knowledgeBaseId,
              processingConfiguration,
              {
                onUploadProgress: (event) => {
                  const total = event.total && event.total > 0 ? event.total : item.size;
                  updateUploadProgress(item.uid, event.loaded, total);
                },
              },
            )
              .then((result) => {
                finishUploadTask(item.uid, hasDispatchIssue(result.processing) ? 'dispatch_issue' : 'success');
                return result.processing;
              })
              .catch(markFailed);
          }))
          .then((dispatchResults) => {
            if (dispatchResults.some((result) => hasDispatchIssue(result))) {
              messageApi.warning(t('knowledgeBase.uploadModal.dispatchIssue'));
            } else {
              messageApi.success(t('knowledgeBase.uploadSuccess'));
            }
          });
      })
      .then(() => {
        refresh?.();
        if (isRechunkMode) handleClose();
      })
      .catch((error: { errorFields?: { name: (string | number)[] }[] }) => {
        const first = error?.errorFields?.[0];
        if (!first?.name?.length) return;
        const section = fieldSectionMap[String(first.name[0])];
        if (section && isConfigSectionKey(section) && section !== activeSection) {
          // Switch sections synchronously to ensure fields are mounted before scrolling to them.
          flushSync(() => setActiveSection(section));
        }
        form.scrollToField(first.name, { behavior: 'smooth', block: 'center', focus: true });
      })
      .finally(() => setLoading(false));
  };

  return {
    form,
    visible,
    loading,
    activeSection,
    setActiveSection,
    fileList,
    isRechunkMode,
    isParentChildMode,
    customModels,
    handleOpen,
    handleAddFiles,
    handleRechunk,
    handleClose,
    handleRemove,
    handleConfirm,
  };
};

export default useUploadFileModal;
