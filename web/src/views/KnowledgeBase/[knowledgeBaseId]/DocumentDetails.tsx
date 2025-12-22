/*
 * @Description: 文档详情
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-15 16:13:47
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-19 20:19:59
 */
import { useEffect, useState, useRef, type FC } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useBreadcrumbManager, type BreadcrumbPath } from '@/hooks/useBreadcrumbManager';
import { Button, Spin, message, Switch } from 'antd';
import { getDocumentDetail, getDocumentChunkList, downloadFile, updateDocument, updateDocumentChunk, createDocumentChunk } from '@/api/knowledgeBase';
import type { KnowledgeBaseDocumentData, RecallTestData } from '@/views/KnowledgeBase/types';
import { formatDateTime } from '@/utils/format';
import InfoPanel, { type InfoItem } from '../components/InfoPanel';
import RecallTestResult from '../components/RecallTestResult';
import SearchInput from '@/components/SearchInput';
import DocumentPreview from '@/components/DocumentPreview';
import InsertModal, { type InsertModalRef } from '../components/InsertModal';
import exitIcon from '@/assets/images/knowledgeBase/exit.png';
const imagePath = 'https://devapi.mem.redbearai.com'
const DocumentDetails: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { knowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const location = useLocation();
  const { updateBreadcrumbs } = useBreadcrumbManager({
    breadcrumbType: 'detail'
  });
  const { 
    documentId, 
    parentId: locationParentId, 
    breadcrumbPath 
  } = location.state as { 
    documentId: string; 
    parentId?: string; 
    breadcrumbPath?: BreadcrumbPath;
  };
  const [loading, setLoading] = useState(false);
  const [document, setDocument] = useState<KnowledgeBaseDocumentData | null>(null);
  const [chunkList, setChunkList] = useState<RecallTestData[]>([]);
  const [infoItems, setInfoItems] = useState<InfoItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [chunkLoading, setChunkLoading] = useState(false);
  const [keywords, setKeywords] = useState('');
  const [fileUrl, setFileUrl] = useState('');
  const [parserMode, setParserMode] = useState(0);
  const insertModalRef = useRef<InsertModalRef>(null);
  const isManualRefreshRef = useRef(false);
  
  useEffect(() => {
    if (documentId) {
      fetchDocumentDetail();
    }
  }, [documentId]);

  // 更新面包屑
  useEffect(() => {
    if (breadcrumbPath) {
      updateBreadcrumbs(breadcrumbPath);
    }
  }, [breadcrumbPath, updateBreadcrumbs]);

  // 当文档加载完成且 progress === 1 时，加载分块列表
  useEffect(() => {
    if (document && document.progress === 1 && !isManualRefreshRef.current) {
      ChunkList();
    }
    // 重置标志
    isManualRefreshRef.current = false;
  }, [document]);

  // 监听 keywords 变化，重新搜索
  useEffect(() => {
    if (documentId && keywords && document?.progress === 1) {
      setPage(1); // 重置页码
      setChunkList([]); // 清空列表
      ChunkList(1, false); // 重新加载第一页
    }
  }, [keywords]);



  const formatDocumentInfo = (doc: KnowledgeBaseDocumentData): InfoItem[] => {
    return [
      {
        key: 'file_name',
        label: t('knowledgeBase.fileName') || '文件名',
        value: doc.file_name ?? '-',
      },
      {
        key: 'status',
        label: t('knowledgeBase.status') || '进度',
        value: doc.progress === 1 ? t('knowledgeBase.progressComplete') : t('knowledgeBase.progressing') ?? '-',
      },
      {
        key: 'chunk_num',
        label: t('knowledgeBase.chunk_num') || '分块数量',
        value: doc.chunk_num ?? 0,
      },
      {
        key: 'parser_id',
        label: t('knowledgeBase.processingMode') || '处理模式',
        value: doc.parser_id ?? '-',
      },
      {
        key: 'created_at',
        label: t('knowledgeBase.created_at') || '创建时间',
        value: formatDateTime(doc.created_at, 'YYYY-MM-DD HH:mm:ss'),
      },
      {
        key: 'updated_at',
        label: t('knowledgeBase.updated_at') || '更新时间',
        value: formatDateTime(doc.updated_at, 'YYYY-MM-DD HH:mm:ss'),
      },
    ].filter((item) => item.value !== null && item.value !== undefined && item.value !== '');
  };

  const fetchDocumentDetail = async () => {
    if (!documentId) return;
    setLoading(true);
    try {
      const response = await getDocumentDetail(documentId);
      setDocument(response);
      setInfoItems(formatDocumentInfo(response));
      const url = `${imagePath}/api/files/${response.file_id}`
      setFileUrl(url);
      setParserMode(response?.parser_config?.auto_questions || 0)
      // ChunkList 会在 useEffect 中根据 document.progress 自动调用
    } catch (error) {
      console.error('获取文档详情失败:', error);
      message.error(t('common.loadFailed') || '加载失败');
    } finally {
      setLoading(false);
    }
  };
  const ChunkList = async (pageNum: number = 1, append: boolean = false, force: boolean = false) => {
    if (!documentId) return;
    
    // 如果不是强制刷新，且正在加载中，则跳过
    if (!force && chunkLoading) {
      return;
    }
    
    // 只有当文档处理完成时才获取分块列表
    if (document && document.progress !== 1) {
      return;
    }
    setChunkLoading(true);
    try {
      const response = await getDocumentChunkList({ 
        kb_id: knowledgeBaseId, 
        document_id: documentId,
        keywords: keywords || undefined,
        page: pageNum,
        pagesize: 20,
        _t: force ? Date.now() : undefined, // 强制刷新时添加时间戳破坏缓存
      });
      
      // 转换数据格式以匹配 RecallTestData
      const formattedChunks: RecallTestData[] = response.items.map((item: any) => ({
        page_content: item.page_content || item.content || '',
        vector: null,
        metadata: {
          doc_id: item.metadata.doc_id || '',
          file_id: item.metadata.file_id || document?.file_id || '',
          file_name: item.metadata.file_name || document?.file_name || '',
          file_created_at: item.metadata.file_created_at || item.metadata.created_at || '',
          document_id: item.metadata.document_id || documentId || '',
          knowledge_id: item.metadata.knowledge_id || knowledgeBaseId || '',
          sort_id: item.metadata.sort_id || item.id || 0,
          score: item.metadata.score || null, // chunk 列表没有相似度分数
          status: item.metadata.status,
        },
        children: null,
      }));
      
      if (append) {
        setChunkList(prev => [...prev, ...formattedChunks]);
      } else {
        setChunkList(formattedChunks);
      }
      
      setHasMore(response.page?.has_next ?? false);
    } catch (error) {
      console.error('获取文档详情失败:', error);
      message.error(t('common.loadFailed') || '加载失败');
    } finally {
      setChunkLoading(false);
    }
  };

  const loadMoreChunks = () => {
    const nextPage = page + 1;
    setPage(nextPage);
    ChunkList(nextPage, true);
  };

  const handleBack = () => {
    if (knowledgeBaseId && breadcrumbPath) {
      // 返回到知识库详情页，并传递面包屑信息以恢复状态
      const navigationState = {
        fromKnowledgeBaseList: true,
        knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
        navigateToDocumentFolder: locationParentId,
        documentFolderPath: breadcrumbPath.documentFolderPath,
        timestamp: Date.now(), // 添加时间戳确保状态变化
      };
      navigate(`/knowledge-base/${knowledgeBaseId}/private`, { state: navigationState });
    } else if (knowledgeBaseId) {
      // 降级处理：直接跳转到知识库详情页
      navigate(`/knowledge-base/${knowledgeBaseId}/private`);
    }
  };
  const handleSearch = (value?: string) => {
    setKeywords(value || '');
  };
  const handleInsert = () => {
    if (!documentId) {
      message.error(t('knowledgeBase.documentIdRequired') || '文档ID不能为空');
      return;
    }
    insertModalRef.current?.handleOpen(documentId);
  };

  // 处理插入/编辑内容
  const handleInsertContent = async (_docId: string, content: string, chunkId?: string): Promise<boolean> => {
    try {
      if (chunkId) {
        // 编辑模式：更新现有块
        const response = await updateDocumentChunk(knowledgeBaseId || '', documentId, chunkId, { content });
        
        // 直接更新前端列表，不等待后端缓存刷新
        setChunkList(prev => prev.map(item => 
          item.metadata?.doc_id === chunkId 
            ? { ...item, page_content: response.page_content || content }
            : item
        ));
        
        // 编辑模式返回特殊标记，告诉 InsertModal 不要调用 onSuccess
        return true;
      } else {
        // 插入模式：创建新块
        await createDocumentChunk(knowledgeBaseId || '', documentId, { content });
        return true;
      }
    } catch (error) {
      console.error('操作失败:', error);
      return false;
    }
  };

  // 处理点击文本块
  const handleChunkClick = (item: RecallTestData, index: number) => {
    if (!documentId) return;
    const chunkId = String(item.metadata?.doc_id || index);
    insertModalRef.current?.handleOpen(documentId, item.page_content, chunkId);
  };

  // 插入成功后的回调（仅用于插入新块，编辑操作已在 handleInsertContent 中同步更新）
  const handleInsertSuccess = () => {
    // 设置手动刷新标志，防止 useEffect 重复调用
    isManualRefreshRef.current = true;
    
    // 重置页码
    setPage(1);
    
    // 等待后端处理完成，然后重新加载数据（仅用于插入新块的情况）
    setTimeout(() => {
      ChunkList(1, false, true).then(() => {
        return fetchDocumentDetail();
      }).catch(err => {
        console.error('刷新失败:', err);
      });
    }, 1000);
  };
  const handleAdjustmentParameter = () =>{
    if (!knowledgeBaseId || !document) return;
    const targetFileId = document.id;
    // 优先使用从 location 传递的 parentId，其次使用 document.parent_id，最后使用 knowledgeBaseId
    const parentId = locationParentId ?? document.parent_id ?? document.kb_id ?? knowledgeBaseId;
    
    navigate(`/knowledge-base/${knowledgeBaseId}/create-dataset`, {
      state: {
        source: 'local',
        knowledgeBaseId,
        parentId,
        startStep: 'parameterSettings',
        fileId: targetFileId,
      },
    });
  }
  const handleDownload = () => {
    if (!document) return;
    downloadFile(document.file_id || '', document.file_name)
  };
  const onChange = (checked: boolean) => {
      updateDocument(documentId, {
        status: checked ? 1 : 0,
      });
  }
  if (loading) {
    return (
      <div className="rb:flex rb:items-center rb:justify-center rb:h-full">
        <Spin size="large" />
      </div>
    );
  }

  if (document?.progress !== 1) {
    return (
      <div className="rb:flex rb:flex-col rb:h-full rb:p-4">
          <div className='rb:flex rb:items-center rb:gap-2 rb:mb-4 rb:cursor-pointer' onClick={handleBack}>
              <img src={exitIcon} alt='exit' className='rb:w-4 rb:h-4' />
              <span className='rb:text-gray-500 rb:text-sm'>{t('common.exit')}</span>
          </div>
          {/* 文档预览 */}
          {fileUrl && (
            <div className='rb:flex-1 rb:border rb:border-[#DFE4ED] rb:bg-white rb:rounded-xl rb:p-4 rb:overflow-hidden'>
              <h3 className="rb:text-sm rb:font-medium rb:mb-3">
                {t('knowledgeBase.documentPreview') || '文档预览'}
              </h3>
              <DocumentPreview 
                fileUrl={fileUrl}
                fileName={document?.file_name}
                fileExt={document?.file_ext}
                height="calc(100% - 40px)"
                mode="google"
                showModeSwitch={true}
              />
            </div>
          )}
      </div>
    );
  }

  return (<>
    <div className="rb:flex rb:flex-col rb:h-full rb:p-4">
      {/* 头部 */}
      <div className="rb:flex rb:flex-col rb:text-left rb:mb-6">
        <div className='rb:flex rb:items-center rb:justify-between'>
            <div className='rb:flex rb:items-center rb:gap-2 rb:mb-4 rb:cursor-pointer' onClick={handleBack}>
                <img src={exitIcon} alt='exit' className='rb:w-4 rb:h-4' />
                <span className='rb:text-gray-500 rb:text-sm'>{t('common.exit')}</span>
            </div>
            
        </div>
        <div className="rb:flex rb:items-center rb:justify-between rb:gap-4">
          
          <div className="rb:flex rb:gap-2 rb:items-center rb:text-xl rb:font-semibold rb:text-gray-800 ">
            {document.file_name || t('knowledgeBase.documentDetails') || '文档详情'}
            <Switch checkedChildren={t('common.enable')} unCheckedChildren={t('common.disable')} defaultChecked={document.status === 1} onChange={onChange}/>
          </div>
          <div className='rb:flex rb:gap-3 rb:items-center'>
              <SearchInput 
                placeholder={t('knowledgeBase.search')} 
                onSearch={handleSearch}
                defaultValue={keywords}
              />
              <Button type='primary' onClick={handleAdjustmentParameter}>{t('knowledgeBase.adjustmentParameter') || '调整参数'}</Button>
              <Button type="primary" onClick={handleInsert}>{t('knowledgeBase.insert') || '插入'}</Button>
          </div>
        </div>
      </div>

      {/* 内容区域 */}
      <div className="rb:flex rb:h-full rb:gap-4 rb:flex-1 rb:overflow-hidden">
        {/* 左侧：文档信息 */}
        <div className='rb:w-80 rb:h-full rb:flex rb:flex-col rb:gap-4 rb:overflow-hidden'>
          <div className='rb:border rb:border-[#DFE4ED] rb:bg-white rb:rounded-xl rb:p-4'>
            <InfoPanel 
              title={t('knowledgeBase.documentInfo') || '文档信息'} 
              items={infoItems}
            />
            <Button type='primary' onClick={handleDownload} className="rb:mt-4 rb:w-full">
              {t('knowledgeBase.downloadOriginal')}
            </Button>
          </div>
        </div>
        
        {/* 右侧：分块列表 */}
        <div 
          id="chunkScrollableDiv"
          className="rb:flex-1 rb:bg-white rb:rounded-lg rb:border rb:border-gray-200 rb:p-6 rb:overflow-y-auto"
        >
          <h2 className="rb:text-lg rb:font-medium rb:mb-4">
            {t('knowledgeBase.chunkList') || '分块列表'}
          </h2>
          <RecallTestResult 
            
            data={chunkList} 
            showEmpty={false}
            hasMore={hasMore}
            loadMore={loadMoreChunks}
            loading={chunkLoading}
            scrollableTarget="chunkScrollableDiv"
            editable={true}
            onItemClick={handleChunkClick}
            parserMode={parserMode}
          />
        </div>
      </div>
      
      {/* 插入内容弹窗 */}
      <InsertModal 
        ref={insertModalRef}
        onInsert={handleInsertContent}
        onSuccess={handleInsertSuccess}
      />
    </div>
  </>);
};

export default DocumentDetails;

