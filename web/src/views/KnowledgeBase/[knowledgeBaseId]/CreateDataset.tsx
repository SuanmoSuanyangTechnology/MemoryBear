import {  useMemo,useRef, useState, useEffect } from 'react';
import { Button, Flex, Radio, Steps, Modal, Input, Spin, message, Checkbox, Select, Form} from 'antd';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import Table, { type TableRef } from '@/components/Table'
import type { AnyObject } from 'antd/es/_util/type';
import type { UploadFileResponse,KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile } from 'antd';
import UploadFiles from '@/components/Upload/UploadFiles';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { uploadFile, getDocumentList, parseDocument, updateDocument, deleteDocument, createDocumentAndUpload } from '@/api/knowledgeBase';
import exitIcon from '@/assets/images/knowledgeBase/exit.png';

import SliderInput from '@/components/SliderInput';
import DelimiterSelector from '../components/DelimiterSelector';
const { confirm } = Modal
const { TextArea } = Input;

  const style: React.CSSProperties = {
    display: 'flex',
    gap: 16,
  };
  const radioWrapperBaseStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'flex-start',
    columnGap: 14, // 点与文字更宽的间距
    width: '100%',
    border: '1px solid #E5E5E5',
    borderRadius: 8,
    padding: 16,
  };
  const getActiveRadioStyle = (active: boolean): React.CSSProperties => ({
    ...radioWrapperBaseStyle,
    border: active ? '1px solid #1677ff' : radioWrapperBaseStyle.border,
  });


type SourceType = 'local' | 'link' | 'text';
type ProcessingMethod = 'directBlock' | 'qaExtract';
type ParameterSettings = 'defaultSettings' | 'customSettings';
const stepKeys = ['selectFile', 'parameterSettings', 'dataPreview', 'confirmUpload'] as const;
type StepKey = typeof stepKeys[number];

const stepIndexMap: Record<StepKey, number> = {
  selectFile: 0,
  parameterSettings: 1,
  dataPreview: 2,
  confirmUpload: 3,
};

interface CreateDatasetLocationState {
  source?: SourceType;
  knowledgeBaseId?: string;
  parentId?: string;
  startStep?: StepKey;
  fileId?: string | string[];
  fileIds?: string | string[];
}
interface ContentFormData {
  title: string;
  content: string;
}
const CreateDataset = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { knowledgeBaseId: routeKnowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const location = useLocation();
  const locationState = (location.state ?? {}) as CreateDatasetLocationState;
  const source = (locationState.source ?? 'local') as SourceType;
  const knowledgeBaseId = locationState.knowledgeBaseId || routeKnowledgeBaseId;
  const parentId = locationState.parentId;
  const initialStepKey = locationState.startStep ?? 'selectFile';
  const initialFileIds = (() => {
    const fileIds = locationState.fileIds || locationState.fileId;
    if (!fileIds) return [];
    return Array.isArray(fileIds) ? fileIds : [fileIds];
  })();
  const [current, setCurrent] = useState<number>(stepIndexMap[initialStepKey]);
  const tableRef = useRef<TableRef>(null);

  const [form] = Form.useForm<ContentFormData>();
  const [data, setData] = useState<KnowledgeBaseDocumentData[]>([]);
  const [rechunkFileIds, setRechunkFileIds] = useState<string[]>(initialFileIds);

  const [pollingLoading, setPollingLoading] = useState<boolean>(false);
  const pollingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [delimiter, setDelimiter] = useState<string | undefined>(undefined);
  const [blockSize, setBlockSize] = useState<number>(130);
  const [processingMethod, setProcessingMethod] = useState<ProcessingMethod>('directBlock');
  const [parameterSettings, setParameterSettings] = useState<ParameterSettings>('defaultSettings');
  const [pdfEnhancementEnabled, setPdfEnhancementEnabled] = useState<boolean>(true);
  const [pdfEnhancementMethod, setPdfEnhancementMethod] = useState<string>('deepdoc');
  const [messageApi, contextHolder] = message.useMessage();
  const fileType = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'md', 'htm', 'html', 'json', 'ppt', 'pptx', 'txt','png','jpg','mp3','mp4','mov','wav']
  const steps = useMemo(
    () => [
      { title: t('knowledgeBase.selectFile') },
      { title: t('knowledgeBase.parameterSettings') },
      // { title: t('knowledgeBase.dataPreview') }, // 暂时隐藏第三步
      { title: t('knowledgeBase.confirmUpload') },
    ],
    [t],
  );
  // 存储每个文件的 AbortController，用于取消上传
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
  const uploadRef = useRef<{ fileList: UploadFile[]; clearFiles: () => void }>(null);
  console.log('上传文件',uploadRef.current?.fileList.length)
  const handleNext = async () => {
    // 暂时隐藏第三步：调整步骤索引（0->1->2 对应 选择文件->参数设置->确认上传）
    let nextStep = current + 1;
    
    if(nextStep === 1 && source === 'local') {
      // 检查是否有文件已上传
      if (rechunkFileIds.length === 0) {
        // 如果没有文件，提示用户先上传文件
        Modal.warning({
          title: t('common.warning') || '提示',
          content: t('knowledgeBase.pleaseUploadFileFirst') || '请先上传文件',
        });
        return; // 不进入下一步
      }
    }else if(nextStep === 1 && source === 'text'){
        try {
            const values = await form.validateFields();
            // setLoading(true);

            // TODO: 这里需要调用相应的API来保存内容
            const params = {
              // ...values,
              kb_id: knowledgeBaseId,
              parent_id: parentId,
            };
            const response = await createDocumentAndUpload(values, params)
            if(response) {
                setRechunkFileIds([response.id])
            }
            
          } catch (err) {
              messageApi.error(t('knowledgeBase.createContentError'));
          } finally {
            // setLoading(false);
          }
    }
    
    // 从参数设置进入确认上传时的处理
    if(current === 1 && nextStep === 2) {
      // debugger
        // handlePreview(data[0],0) 
        if(parameterSettings === 'customSettings' || processingMethod === 'qaExtract' || pdfEnhancementEnabled){
            rechunkFileIds.map((id) => {
                const params = {
                  progress: 0,
                  parser_config: {
                      layout_recognize: pdfEnhancementMethod || 'DeepDOC',
                      delimiter: delimiter,
                      chunk_token_num: blockSize,
                      auto_questions: processingMethod === 'directBlock' ? 0 : 1,
                  }
                }
                updateDocument(id, params)
            })
        }

        // 立即执行一次，加载文档列表用于预览（不自动返回）
        pollDocumentStatus(false);
    }
    
    // 限制最大步骤为 2（确认上传）
    setCurrent(Math.min(nextStep, 2));
  };
  const handlePrev = () => setCurrent((c) => Math.max(c - 1, 0));
  
  // 开始上传：触发文档解析并启动轮询
  const handleStartUpload = () => {
    if (rechunkFileIds.length === 0) {
      Modal.warning({
        title: t('common.warning') || '提示',
        content: t('knowledgeBase.pleaseUploadFileFirst') || '请先上传文件',
      });
      return;
    }

    // 显示确认弹框
    confirm({
      title: t('knowledgeBase.startUploadConfirmTitle') || '开始处理文档',
      content: t('knowledgeBase.startUploadConfirmContent') || '文档处理将在后台进行，您可以选择立即返回列表页或停留在此页面查看处理进度。',
      okText: t('knowledgeBase.returnToList') || '返回列表页',
      cancelText: t('knowledgeBase.stayOnPage') || '停留在此页',
      onOk: () => {
        // 用户选择返回列表页 - 不显示 loading，直接跳转
        startProcessing(true);
      },
      onCancel: () => {
        // 用户选择停留在当前页 - 显示 loading 并开始轮询
        console.log('用户选择停留，开始显示 loading');
        setPollingLoading(true);
        
        // 延迟一点时间让用户看到 loading 效果，然后开始处理
        setTimeout(() => {
          startProcessing(false);
        }, 100);
      },
    });
  };

  // 实际开始处理的函数
  const startProcessing = (autoReturnToList: boolean) => {
    // 触发文档解析
    rechunkFileIds.map((id) => {
      parseDocument(id, {});
    });

    if (autoReturnToList) {
      // 用户选择立即返回，直接跳转（不显示 loading）
      console.log('用户选择立即返回列表页');
      handleBack();
    } else {
      // 用户选择停留，启动轮询查看进度（loading 已在 onCancel 中设置）
      console.log('用户选择停留查看进度');
      
      // 立即执行一次轮询（启用自动返回）
      pollDocumentStatus(true);

      // 然后每3秒执行一次（启用自动返回）
      pollingTimerRef.current = setInterval(() => {
        pollDocumentStatus(true);
      }, 3000);
    }
  };
  const handleDelete = (record: AnyObject) => {
        confirm({
            title: t('common.deleteWarning'),
            content: t('common.deleteWarningContent', { content: record.name }),
          onOk: async () => {
              await deleteDocument(record.id);
              
              // 删除成功，从 rechunkFileIds 中移除该 id
              setRechunkFileIds((prev) => prev.filter((id) => id !== record.id));
              
              // 刷新列表
              messageApi.success(t('common.deleteSuccess'));
              tableRef.current?.loadData();
            
          },
          onCancel: () => {
            console.log('取消删除');
          },
      });
  }
  // 表格列配置
  const columns: ColumnsType = [
    {
      title: t('knowledgeBase.name'),
      dataIndex: 'file_name',
      key: 'file_name'
    },
    
    {
      title: t('knowledgeBase.status'),
      dataIndex: 'progress',
      key: 'progress',
      render: (value: number, record: any) => {
        return (
          <span className="rb:text-xs rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded rb:items-center rb:text-[#212332] rb:py-1 rb:px-2">
            <span className="rb:inline-block rb:w-[5px] rb:h-[5px] rb:mr-2 rb:rounded-full" style={{ backgroundColor: value === 1 ? '#369F21' : '#FF8A4C' }}></span>
            <span>{value === 1 ? t('knowledgeBase.completed') : value === 0 ? t('knowledgeBase.pending') : t('knowledgeBase.processing')}</span>
          </span>
        );
      }
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record) => (
        <Button type='text' danger onClick={() => handleDelete(record)}>{t('common.delete')}</Button>
      ),
    },
  ];
  // 检查媒体文件时长的辅助函数
  const checkMediaDuration = (file: File): Promise<number> => {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const media = document.createElement(file.type.startsWith('video/') ? 'video' : 'audio');
      
      media.onloadedmetadata = () => {
        URL.revokeObjectURL(url);
        resolve(media.duration);
      };
      
      media.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error(`${t('knowledgeBase.unableReadFile')}`));
      };
      
      media.src = url;
    });
  };

  // 上传文件
  const handleUpload = async (options: UploadRequestOption) => {
    const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
    
    // 创建 AbortController 用于取消上传
    const abortController = new AbortController();
    const fileUid = (file as any).uid;
    abortControllersRef.current.set(fileUid, abortController);

    // 获取文件扩展名
    const fileExtension = (file as File).name.split('.').pop()?.toLowerCase();
    const mediaExtensions = ['mp3', 'mp4', 'mov', 'wav'];
    
    // 如果是媒体文件，进行大小和时长检查
    if (fileExtension && mediaExtensions.includes(fileExtension)) {
      const fileSizeInMB = (file as File).size / (100 * 1024);
      
      // 检查文件大小（50MB限制）
      if (fileSizeInMB > 100) {
        messageApi.error(`${t('knowledgeBase.sizeLimitError')}：${fileSizeInMB.toFixed(2)}MB`);
        onError?.(new Error(`${t('knowledgeBase.fileSizeExceeds')}`));
        abortControllersRef.current.delete(fileUid);
        return;
      }
      
      try {
        // 检查媒体时长（150秒限制）
        const duration = await checkMediaDuration(file as File);
        if (duration > 150) {
          messageApi.error(`${t('knowledgeBase.fileDurationLimitError')}：${Math.round(duration)}秒`);
          onError?.(new Error(`${t('knowledgeBase.fileDurationExceeds')}`));
          abortControllersRef.current.delete(fileUid);
          return;
        }
      } catch (error) {
        messageApi.error(`${t('knowledgeBase.unableReadFile')}`);
        onError?.(error as Error);
        abortControllersRef.current.delete(fileUid);
        return;
      }
    }

    const formData = new FormData();
    formData.append(filename, file as File);
    if (knowledgeBaseId) {
      formData.append('kb_id', knowledgeBaseId);
    }
    if (parentId) {
      formData.append('parent_id', parentId);
    }

    uploadFile(formData, {
      kb_id: knowledgeBaseId,
      parent_id: parentId,
      signal: abortController.signal,
      onUploadProgress: (event) => {
        if (!event.total) return;
        const percent = Math.round((event.loaded / event.total) * 100);
        onProgress?.({ percent }, file);
      },
    })
      .then((res: UploadFileResponse) => {
        onSuccess?.(res, new XMLHttpRequest());
        if (res?.id) {
          setRechunkFileIds((prev) => {
            if (prev.includes(res.id)) return prev;
            const next = [...prev, res.id];
            return next;
          });
        }
      })
      .catch((error) => {
        // 移除 AbortController
        abortControllersRef.current.delete(fileUid);
        
        // 如果是用户主动取消，不显示错误信息
        if (error.name === 'AbortError' || error.code === 'ERR_CANCELED') {
          console.log('上传已取消:', (file as File).name);
          return;
        }
        onError?.(error as Error);
      });
  };


  // 轮询检查文档处理状态
  // autoReturn: 是否在所有文档完成时自动返回列表页
  const pollDocumentStatus = (autoReturn: boolean = false) => {
    console.log('开始轮询文档状态，当前 pollingLoading:', pollingLoading);
    
    if (!knowledgeBaseId || !parentId || rechunkFileIds.length === 0) {
      console.log('轮询条件不满足，退出');
      return;
    }

    // 获取文档列表检查是否全部完成，并刷新表格数据
    getDocumentList(knowledgeBaseId, {
      document_ids: rechunkFileIds.join(','),
    })
    .then((res: any) => {
      const documents = res.items || [];
      setData(documents);
      
      // 只在 confirmUpload 步骤刷新表格数据
      if (current === 2) {
        tableRef.current?.loadData();
      }
      
      console.log('documents', documents);
      // 检查是否所有文档的 progress 都为 1
      const allCompleted = documents.every((doc: KnowledgeBaseDocumentData) => doc.progress === 1);
      
      console.log('轮询状态:', allCompleted);
      
      // 检查是否所有文档都完成了
      // debugger
      if (allCompleted) {
        // 清除定时器和 loading 状态
        if (pollingTimerRef.current) {
          clearInterval(pollingTimerRef.current);
          pollingTimerRef.current = null;
        }
        
        // 延迟清除 loading，让用户看到完成状态
        setTimeout(() => {
          setPollingLoading(false);
        }, 1000);
        
        // 只有在 autoReturn 为 true 时才自动返回
        if (autoReturn) {
          // 延迟 2 秒后跳转，让用户看到完成状态
          console.log('所有文档处理完成，2秒后返回列表页');
          setTimeout(() => {
            handleBack();
          }, 2000);
        } else {
          console.log('所有文档处理完成，用户可手动操作');
        }
      } else {
        // 如果还有文档在处理中，确保 loading 状态保持
        console.log('还有文档在处理中，保持 loading 状态');
      }
    })
    .catch((error) => {
      console.error('轮询文档状态失败:', error);
      setPollingLoading(false);
    });
  };
  const handleBack = () => {
    if (knowledgeBaseId) {
      navigate(`/knowledge-base/${knowledgeBaseId}/private`, {
        state: {
          refresh: true,
          timestamp: Date.now(), // 添加时间戳确保每次都是新的 state
          // 保持返回到原来的文档文件夹位置
          navigateToDocumentFolder: parentId !== knowledgeBaseId ? parentId : undefined,
        },
      });
    } else {
      console.warn('缺少路由参数，无法返回');
    }
  };
  const handleChange = (value: number | null) =>{
      if (value !== null) {
        setBlockSize(value);
      }
  }
  // 删除已上传的文件
  const handleDeleteFile = async (fileId: string) => {
    try {
      await deleteDocument(fileId);
      // 删除成功，从 rechunkFileIds 中移除该 id
      setRechunkFileIds((prev) => prev.filter((id) => id !== fileId));
      console.log(`${t('common.deleteSuccess')}`);
    } catch (error) {
      messageApi.error(`${t('common.deleteFailed')}`);
    }
  };
  // 当从其他页面跳转过来且带有 fileIds 时，加载对应的文档数据
  // useEffect(() => {
  //   if (initialFileIds.length > 0 && initialStepKey !== 'selectFile' && knowledgeBaseId && parentId) {
  //     // 加载文档列表数据
  //     getDocumentList(knowledgeBaseId,{
  //       document_ids: initialFileIds.join(','),
  //     })
  //     .then((res: any) => {
  //       const documents = res.items || [];
  //       setData(documents);
  //     })
  //     .catch((error) => {
  //       console.error('加载文档列表失败:', error);
  //     });
  //   }
  // }, []);

  // 清理函数：组件卸载时清除定时器和 loading 状态
  useEffect(() => {
    return () => {
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
      setPollingLoading(false);
    };
  }, []);

  // 监听路由变化，确保在页面切换时清理状态
  useEffect(() => {
    return () => {
      // 页面卸载时清理状态
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
      setPollingLoading(false);
    };
  }, [location.pathname]);

  return (
    <>
      {contextHolder}
    
    <div className='rb:p-6 rb:pt-2 rb:h-full'>
      {/* <Typography.Title level={4} className='rb:!m-0 rb:!mb-4'>
        {t('knowledgeBase.createA') + ' ' + t('knowledgeBase.dataset')}
      </Typography.Title> */}
      <div className='rb:flex rb:items-center rb:gap-2 rb:mb-4 rb:cursor-pointer' onClick={handleBack}>
          <img src={exitIcon} alt='exit' className='rb:w-4 rb:h-4' />
          <span className='rb:text-gray-500 rb:text-sm'>{t('common.exit')}</span>
      </div>
      <div className='rb:px-24 rb:py-5  rb:bg-[#FBFDFF] rb:rounded-lg rb:border rb:border-[#DFE4ED]'>
          <Steps current={current} items={steps} />
      </div>  
      

      {current === 0 && (
        <div className='rb:flex rb:w-full rb:mt-10'>
            {source && source === 'local' && (
                <UploadFiles 
                  ref={uploadRef}
                  isCanDrag={true} 
                  fileSize={100} 
                  multiple={true} 
                  maxCount={99} 
                  fileType={fileType} 
                  customRequest={handleUpload}
                  onChange={(fileList) => {
                    console.log('文件列表变化:', fileList);
                  }}
                  onRemove={async (file) => {
                      // 如果文件正在上传，取消上传
                      const fileUid = file.uid;
                      const abortController = abortControllersRef.current.get(fileUid);
                      if (abortController) {
                        abortController.abort();
                        abortControllersRef.current.delete(fileUid);
                        
                      }
                      console.log('文件移除前:', uploadRef.current?.fileList);
                      // 如果文件已经上传成功，删除服务器上的文件并从rechunkFileIds中移除对应的ID
                      if (file.response?.id) {
                        try {
                          await deleteDocument(file.response.id);
                          setRechunkFileIds(prev => prev.filter(id => id !== file.response.id));
                        } catch (error) {
                          console.error('删除文件失败:', error);
                          messageApi.error('删除文件失败');
                        }
                      }
                      
                      return true; // 允许移除文件
                    }} />
            )}
            {source && source === 'link' && (
                <div className='rb:flex rb:w-full rb:flex-col rb:mt-10 rb:px-40'>

                    <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-3'>
                        {t('knowledgeBase.webLink')}
                    </div>
                    <TextArea  rows={6} placeholder={t('knowledgeBase.webLinkPlaceholder')} />
                    <div className='rb:text-sm rb:text-gray-500 rb:mt-3 rb:max-w-[558px]'>
                        {t('knowledgeBase.webLinkDesc')}
                    </div>
                    <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mt-10 rb:mb-3'>
                        {t('knowledgeBase.selectorTutorial')}
                    </div>
                    <Input className='rb:w-full' placeholder={t('knowledgeBase.webLinkPlaceholder')}/>
                </div>
            )}
            {source && source === 'text' && (
                <div className='rb:flex rb:w-full rb:flex-col rb:mt-10 rb:px-40'>
                    <Form form={form} layout="vertical">
                        <Form.Item
                          name="title"
                          label={t('knowledgeBase.title')}
                          rules={[{ required: true, message: t('knowledgeBase.pleaseEnterTitle') }]}
                        >
                          <Input placeholder={t('knowledgeBase.pleaseEnterTitle')} />
                        </Form.Item>

                        <Form.Item
                          name="content"
                          label={t('knowledgeBase.customContent')}
                          rules={[{ required: true, message: t('knowledgeBase.pleaseEnterContent') }]}
                        >
                          <Input.TextArea
                            placeholder={t('knowledgeBase.pleaseEnterContent')}
                            rows={8}
                            showCount
                            maxLength={5000}
                          />
                        </Form.Item>
                      </Form>
                    {/* <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-3'>
                        {t('knowledgeBase.customText')}
                    </div>
                    <Input className='rb:w-full' placeholder={t('knowledgeBase.webLinkPlaceholder')}/>
                    <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mt-10 rb:mb-3'>
                        {t('knowledgeBase.customContent')}
                    </div>
                    <TextArea  rows={6} placeholder={t('knowledgeBase.webLinkPlaceholder')} /> */}
                </div>
            )}
        </div>
      )}

      {current === 1 && (
        <div className='rb:flex rb:flex-col rb:mt-10 rb:px-40'>
            {rechunkFileIds.length > 0 && (
              <div className='rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded rb:px-3 rb:py-2 rb:mb-4 rb:text-xs rb:text-gray-600 rb:flex rb:flex-wrap rb:gap-2'>
                  <span className='rb:text-gray-700 rb:font-medium'>{t('knowledgeBase.rechunking')}:</span>
                  {rechunkFileIds.map((id) => (
                    <span key={id} className='rb:px-2 rb:py-0.5 rb:bg-white rb:border rb:border-[#DFE4ED] rb:rounded'>{id}</span>
                  ))}
              </div>
            )}
            <div className='rb:text-base rb:font-medium rb:text-gray-800 rb:mt-4'>
                {t('knowledgeBase.fileParsingSettings')}
            </div>
            <div className='rb:mt-4'>
              <div 
                className={`rb:flex rb:items-center rb:w-full rb:border rb:rounded-lg rb:p-4 rb:cursor-pointer ${
                  pdfEnhancementEnabled ? 'rb:border-blue-500' : 'rb:border-gray-300'
                }`}
                // onClick={() => setPdfEnhancementEnabled(!pdfEnhancementEnabled)}
              >
                <Checkbox 
                  checked={pdfEnhancementEnabled}
                  onChange={(e) => setPdfEnhancementEnabled(e.target.checked)}
                  className='rb:mr-3'
                />
                <span className='rb:text-base rb:font-medium rb:text-gray-800 rb:pl-[22px]'>
                  {t('knowledgeBase.pdfEnhancementAnalysis')}
                </span>
                {pdfEnhancementEnabled && (
                <div className='rb:ml-10'>
                  <Select
                    value={pdfEnhancementMethod}
                    onChange={(value) => setPdfEnhancementMethod(value)}
                    className='rb:w-48'
                    options={[
                      { value: 'deepdoc', label: 'DeepDoc' },
                      { value: 'mineru', label: 'MinerU' },
                      { value: 'textln', label: 'TextLN' }
                    ]}
                  />
                </div>
              )}
              </div>
              
            </div>
            <div className='rb:text-base rb:font-medium rb:text-gray-800 rb:mt-6'>
                {t('knowledgeBase.dataProcessingSettings')}
            </div>
            <div className='rb:font-medium rb:text-gray-500 rb:mt-4 rb:mb-3'>
                {t('knowledgeBase.processingMethod')}
            </div>
            <Radio.Group
                value={processingMethod}
                onChange={(e) => setProcessingMethod(e.target.value)}
                style={style}
            >
                <Radio value='directBlock' style={getActiveRadioStyle(processingMethod === 'directBlock')}>
                    <Flex gap='small' vertical>
                        <span className='rb:text-base rb:font-medium rb:text-gray-800'>
                            {t('knowledgeBase.directBlock')}
                        </span>
                    </Flex>
                </Radio>
                <Radio value='qaExtract' style={getActiveRadioStyle(processingMethod === 'qaExtract')}>
                    <Flex gap='small' vertical>
                        <span className='rb:text-base rb:font-medium rb:text-gray-800'>
                        {t('knowledgeBase.qaExtract')}
                        </span>
                    </Flex>
                </Radio>
            </Radio.Group>
            <div className='rb:font-medium rb:text-gray-500 rb:mt-4 rb:mb-3'>
                {t('knowledgeBase.parameterSettings')}
            </div>
            <Radio.Group
                value={parameterSettings}
                onChange={(e) => setParameterSettings(e.target.value)}
                style={style}
            >
                <Radio value='defaultSettings' style={getActiveRadioStyle(parameterSettings === 'defaultSettings')}>
                    <Flex gap='small' vertical>
                        <span className='rb:text-base rb:font-medium rb:text-gray-800'>
                            {t('knowledgeBase.default')}
                        </span>
                        <span className='rb:text-3 rb:text-gray-500'>{t('knowledgeBase.defaultSettings')}</span>
                    </Flex>
                </Radio>
                <Radio value='customSettings' style={getActiveRadioStyle(parameterSettings === 'customSettings')}>
                    <Flex gap='small' vertical>
                        <span className='rb:text-base rb:font-medium rb:text-gray-800'>
                            {t('knowledgeBase.customize')}
                        </span>
                        <span className='rb:text-3 rb:text-gray-500'>{t('knowledgeBase.customSettings')}</span>
                    </Flex>
                </Radio>
            </Radio.Group>
            {parameterSettings === 'customSettings' && ( 
              <div className='rb:flex rb:flex-col rb:mt-5'> 
                  <div className='rb:w-full rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-3'>
                      {t('knowledgeBase.delimiter')}
                  </div>
                  <DelimiterSelector value={delimiter} onChange={setDelimiter} className='rb:mb-5'/>
                  <SliderInput label={t('knowledgeBase.suggestedBlockSize')} max={1024} min={1} step={1} value={blockSize} onChange={handleChange} />
              </div>
              
            )}
        </div>
      )}

      {/* 暂时隐藏第三步：数据预览 */}
      {/* {current === stepIndexMap.dataPreview && (
        <div className='rb:grid rb:grid-cols-2 rb:rounded-xl rb:border rb:border-[#DFE4ED] rb:h-[calc(100%-160px)] rb:bg-[#FBFDFF] rb:mt-4'>
            <div className='rb:border-r rb:h-full rb:overflow-hidden rb:border-[#DFE4ED]'>
                <div className='rb:h-11 rb:w-full rb:text-sm rb:font-medium rb:text-gray-800 rb:px-4 rb:py-3 rb:border-b rb:border-[#DFE4ED]'>
                    {t('knowledgeBase.fileList')}
                </div>
                <div className='rb:flex rb:flex-col rb:h-[calc(100%-44px)] rb:overflow-y-auto'>
                    {data.map((item, index) => (
                        <div key={index} className={`rb:h-11 rb:w-full rb:text-sm rb:text-gray-800 rb:px-4 rb:py-3  rb:hover:text-[#155EEF] rb:cursor-pointer ${curSelectedFileId === index ? styles.textBg + ' ' + styles.active : ''}`}
                            onClick={() => handlePreview(item, index)}>
                            {item.file_name}
                        </div>
                        ))
                    }
                    
                </div>
            </div>
            <div className='rb:h-full rb:overflow-hidden'>
                <div className='rb:flex rb:items-center rb:justify-between rb:h-11 rb:w-full rb:text-sm rb:font-medium rb:text-gray-800 rb:px-4 rb:py-3 rb:border-b rb:border-[#DFE4ED]'>
                    {t('knowledgeBase.dataPreview')}
                    <span className='rb:text-sm rb:text-gray-500'>{t('knowledgeBase.maxPreviewChunks', {count: total, max: chunkData.length})}</span>
                </div>
                <Spin spinning={previewLoading}>
                    <div className='rb:flex rb:flex-col rb:h-[calc(100%-44px)] rb:overflow-y-auto'>
                        {chunkData.length > 0 ? (
                            chunkData.map((item, index) => (
                                <div key={index} className='rb:text-sm rb:text-gray-800 rb:px-4 rb:py-3'
                                    dangerouslySetInnerHTML={{ __html: item.page_content }}
                                />
                            ))
                        ) : (
                            <NoData title={t('knowledgeBase.noChunksToPreview')} 
                                subTitle={t('knowledgeBase.clickToPreview')}
                                image={noDataIcon}
                            />
                        )}
                    </div>
                </Spin>
            </div>
        </div>
      )} */}

      {current === 2 && (
        <Spin spinning={pollingLoading} tip={t('knowledgeBase.processingDocuments') || '正在处理文档...'}>
          <div className='rb:text-sm rb:text-gray-500 rb:mt-4 rb:h-[calc(100%-160px)] rb:overflow-y-auto'>
            {rechunkFileIds.length > 0 ? (
              <Table
                ref={tableRef}
                apiUrl={`/documents/${knowledgeBaseId}/documents`}
                apiParams={{       
                    document_ids: rechunkFileIds.join(','),
                }}
                columns={columns}
                rowKey="id"
              />
            ) : (
              <Table
                ref={tableRef}
                columns={columns}
                rowKey="id"
                initialData={[]}
              />
            )}
          </div>
        </Spin>
      )}

      <div className={`rb:flex rb:gap-3 rb:mt-6 ${current === 1 || (source == 'link' && current === 0) || (source == 'text' && current === 0) ? 'rb:pl-40 rb:mt-10' : ''}`}>
        {current !== 0 && (
            <Button onClick={handlePrev} disabled={current === 0 || pollingLoading}>
            {t('common.previous') || 'Prev'}
            </Button>
        )}
        <Button 
          type='primary' 
          onClick={current === 2 ? handleStartUpload : handleNext}
          disabled={pollingLoading || (current === 0 && rechunkFileIds.length === 0)}
          loading={pollingLoading}
        >
          {current === 2 ? t('knowledgeBase.startUploading') || 'Start Upload' : t('common.next') || 'Next'}
        </Button>
      </div>
    </div>
  </>);
};

export default CreateDataset;

