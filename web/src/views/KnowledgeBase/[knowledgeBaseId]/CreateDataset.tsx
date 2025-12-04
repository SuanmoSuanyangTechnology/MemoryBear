import {  useMemo,useRef, useState, useEffect } from 'react';
import { Button, Flex, Radio, Steps, Modal, Input, Spin,message} from 'antd';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import Table, { type TableRef } from '@/components/Table'
import type { AnyObject } from 'antd/es/_util/type';
import type { UploadFileResponse,KnowledgeBaseDocumentData } from '../types';
import type { ColumnsType } from 'antd/es/table';
import UploadFiles from '@/components/Upload/UploadFiles';
import type { UploadRequestOption } from 'rc-upload/lib/interface';
import { uploadFile, getDocumentList, previewDocumentChunk, parseDocument, updateDocument, deleteDocument } from '../service';
import exitIcon from '@/assets/images/knowledgeBase/exit.png';
import { NoData } from '../components/noData';
import noDataIcon from '@/assets/images/knowledgeBase/noData.png';
import SliderInput from '@/components/SliderInput';
import DelimiterSelector from '../components/DelimiterSelector';
const { confirm } = Modal
const { TextArea } = Input;
import styles from '../index.module.css';
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
  fileId?: string;
  fileIds?: string[];
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
  const initialFileIds = locationState.fileIds ?? (locationState.fileId ? [locationState.fileId] : []);
  const [current, setCurrent] = useState<number>(stepIndexMap[initialStepKey]);
  const tableRef = useRef<TableRef>(null);
  const [data, setData] = useState<KnowledgeBaseDocumentData[]>([]);
  const [chunkData, setChunkData] = useState<any[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [rechunkFileIds, setRechunkFileIds] = useState<string[]>(initialFileIds);
  const [curSelectedFileId, setCurSelectedFileId] = useState<number>(-1);
  const [previewLoading, setPreviewLoading] = useState<boolean>(false);
  const [pollingLoading, setPollingLoading] = useState<boolean>(false);
  const pollingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [delimiter, setDelimiter] = useState<string | undefined>(undefined);
  const [blockSize, setBlockSize] = useState<number>(130);
  const [processingMethod, setProcessingMethod] = useState<ProcessingMethod>('directBlock');
  const [parameterSettings, setParameterSettings] = useState<ParameterSettings>('defaultSettings');
  const [messageApi, contextHolder] = message.useMessage();
  const fileType = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'md', 'htm', 'html', 'json', 'ppt', 'pptx', 'txt','png','jpg']
  const steps = useMemo(
    () => [
      { title: t('knowledgeBase.selectFile') },
      { title: t('knowledgeBase.parameterSettings') },
      // { title: t('knowledgeBase.dataPreview') }, // 暂时隐藏第三步
      { title: t('knowledgeBase.confirmUpload') },
    ],
    [t],
  );
  
  const handleNext = () => {
    // 暂时隐藏第三步：调整步骤索引（0->1->2 对应 选择文件->参数设置->确认上传）
    let nextStep = current + 1;
    
    if(nextStep === 1) {
      // 检查是否有文件已上传
      if (rechunkFileIds.length === 0) {
        // 如果没有文件，提示用户先上传文件
        Modal.warning({
          title: t('common.warning') || '提示',
          content: t('knowledgeBase.pleaseUploadFileFirst') || '请先上传文件',
        });
        return; // 不进入下一步
      }
    }
    
    // 从参数设置进入确认上传时的处理
    if(current === 1 && nextStep === 2) {
        // handlePreview(data[0],0) 
        if(parameterSettings === 'customSettings'){
            rechunkFileIds.map((id) => {
                const params = {
                  parser_config: {
                      layout_recognize:'DeepDOC',
                      delimiter: delimiter,
                      chunk_token_num: blockSize,
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
    debugger

    // 显示确认弹框
    confirm({
      title: t('knowledgeBase.startUploadConfirmTitle') || '开始处理文档',
      content: t('knowledgeBase.startUploadConfirmContent') || '文档处理将在后台进行，您可以选择立即返回列表页或停留在此页面查看处理进度。',
      okText: t('knowledgeBase.returnToList') || '返回列表页',
      cancelText: t('knowledgeBase.stayOnPage') || '停留在此页',
      onOk: () => {
        // 用户选择返回列表页
        startProcessing(true);
      },
      onCancel: () => {
        // 用户选择停留在当前页
        startProcessing(false);
      },
    });
  };

  // 实际开始处理的函数
  const startProcessing = (autoReturnToList: boolean) => {
    // 触发文档解析
    rechunkFileIds.map((id) => {
      parseDocument(id);
    });

    // 开启 loading
    setPollingLoading(true);

    if (autoReturnToList) {
      // 用户选择立即返回，直接跳转
      console.log('用户选择立即返回列表页');
      handleBack();
    } else {
      // 用户选择停留，启动轮询查看进度
      console.log('用户选择停留查看进度');
      
      // 立即执行一次轮询（启用自动返回）
      pollDocumentStatus(true);

      // 然后每3秒执行一次（启用自动返回）
      pollingTimerRef.current = setInterval(() => {
        pollDocumentStatus(true);
      }, 5000);
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
      render: (value: number) => {
        return (
          <span className="rb:text-xs rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded rb:items-center rb:text-[#212332] rb:py-1 rb:px-2">
            <span className="rb:inline-block rb:w-[5px] rb:h-[5px] rb:mr-2 rb:rounded-full" style={{ backgroundColor: value === 1 ? '#369F21' : '#FF8A4C' }}></span>
            <span>{value === 1 ? 'Completed' : 'Processing'}</span>
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
  // 上传文件
  const handleUpload = (options: UploadRequestOption) => {
    const { file, onSuccess, onError, onProgress, filename = 'file' } = options;
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
        onError?.(error as Error);
      });
  };
  // 点击文件 预览分块
  const handlePreview = async(item: KnowledgeBaseDocumentData, index: number) => {
    setCurSelectedFileId(index);
    setPreviewLoading(true);
    try{
        const res = await previewDocumentChunk(knowledgeBaseId ?? '', item.id ?? '');
        setChunkData(res.items || []);
        setTotal(res.page.total || 0);
        console.log('res', res);
    }catch(error) {
        console.log('error', error);
    } finally {
        setPreviewLoading(false);
    }
  }

  // 轮询检查文档处理状态
  // autoReturn: 是否在所有文档完成时自动返回列表页
  const pollDocumentStatus = (autoReturn: boolean = false) => {
    if (!knowledgeBaseId || !parentId || rechunkFileIds.length === 0) {
      return;
    }

    // 刷新 Table 组件的数据（仅在 confirmUpload 步骤）
    if (current === 2) {
      tableRef.current?.loadData();
    }

    // 同时获取文档列表检查是否全部完成
    getDocumentList({
      kb_id: knowledgeBaseId,
      parent_id: parentId,
      document_ids: rechunkFileIds.join(','),
    })
    .then((res: any) => {
      const documents = res.items || [];
      setData(documents);
      
      // 检查是否所有文档的 progress 都为 1
      const allCompleted = documents.every((doc: KnowledgeBaseDocumentData) => doc.progress === 1);
      
      console.log('轮询状态:', documents.map((d: KnowledgeBaseDocumentData) => ({ name: d.file_name, progress: d.progress })));
      
      // 只有在 autoReturn 为 true 且所有文档完成时才自动返回
      if (allCompleted && autoReturn) {
        // 所有文档处理完成，清除定时器和 loading
        if (pollingTimerRef.current) {
          clearInterval(pollingTimerRef.current);
          pollingTimerRef.current = null;
        }
        setPollingLoading(false);
        
        // 延迟 2 秒后跳转，让用户看到完成状态
        console.log('所有文档处理完成，2秒后返回列表页');
        setTimeout(() => {
          handleBack();
        }, 2000);
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

  // 当从其他页面跳转过来且带有 fileIds 时，加载对应的文档数据
  useEffect(() => {
    if (initialFileIds.length > 0 && initialStepKey !== 'selectFile' && knowledgeBaseId && parentId) {
      // 加载文档列表数据
      getDocumentList({
        kb_id: knowledgeBaseId,
        parent_id: parentId,
        document_ids: initialFileIds.join(','),
      })
      .then((res: any) => {
        const documents = res.items || [];
        setData(documents);
      })
      .catch((error) => {
        console.error('加载文档列表失败:', error);
      });
    }
  }, []);

  // 清理函数：组件卸载时清除定时器
  useEffect(() => {
    return () => {
      if (pollingTimerRef.current) {
        clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
      setPollingLoading(false);
    };
  }, []);

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
                <UploadFiles isCanDrag={true} fileSize={50} multiple={true} maxCount={99} fileType={fileType} customRequest={handleUpload} />
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

                    <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mb-3'>
                        {t('knowledgeBase.customText')}
                    </div>
                    <Input className='rb:w-full' placeholder={t('knowledgeBase.webLinkPlaceholder')}/>
                    <div className='rb:text-sm rb:font-medium rb:text-gray-800 rb:mt-10 rb:mb-3'>
                        {t('knowledgeBase.customContent')}
                    </div>
                    <TextArea  rows={6} placeholder={t('knowledgeBase.webLinkPlaceholder')} />
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
            <div className='rb:text-base rb:font-medium rb:text-gray-800'>
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
                apiUrl={`/documents/${knowledgeBaseId}/${parentId}/documents`}
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
          disabled={pollingLoading}
          loading={pollingLoading}
        >
          {current === 2 ? t('knowledgeBase.startUploading') || 'Start Upload' : t('common.next') || 'Next'}
        </Button>
      </div>
    </div>
  </>);
};

export default CreateDataset;

