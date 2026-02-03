/**
 * @Description: Document Details
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

  // Update breadcrumbs
  useEffect(() => {
    if (breadcrumbPath) {
      updateBreadcrumbs(breadcrumbPath);
    }
  }, [breadcrumbPath, updateBreadcrumbs]);

  // Load chunk list when document is loaded and progress === 1
  useEffect(() => {
    if (document && document.progress === 1 && !isManualRefreshRef.current) {
      ChunkList();
    }
    // Reset flag
    isManualRefreshRef.current = false;
  }, [document]);

  // Listen to keywords changes and re-search
  useEffect(() => {
    if (documentId && keywords && document?.progress === 1) {
      setPage(1); // Reset page number
      setChunkList([]); // Clear list
      ChunkList(1, false); // Reload first page
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
      // ChunkList will be called automatically in useEffect based on document.progress
    } catch (error) {
      console.error('Failed to fetch document details:', error);
      message.error(t('common.loadFailed') || '加载失败');
    } finally {
      setLoading(false);
    }
  };
  const ChunkList = async (pageNum: number = 1, append: boolean = false, force: boolean = false) => {
    if (!documentId) return;
    
    // Skip if not force refresh and already loading
    if (!force && chunkLoading) {
      return;
    }
    
    // Only fetch chunk list when document processing is complete
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
        _t: force ? Date.now() : undefined, // Add timestamp to break cache when force refresh
      });
      
      // Convert data format to match RecallTestData
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
          score: item.metadata.score || null, // Chunk list has no similarity score
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
      console.error('Failed to fetch document details:', error);
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
      // Return to knowledge base detail page and pass breadcrumb info to restore state
      const navigationState = {
        fromKnowledgeBaseList: true,
        knowledgeBaseFolderPath: breadcrumbPath.knowledgeBaseFolderPath,
        navigateToDocumentFolder: locationParentId,
        documentFolderPath: breadcrumbPath.documentFolderPath,
        timestamp: Date.now(), // Add timestamp to ensure state change
      };
      navigate(`/knowledge-base/${knowledgeBaseId}/private`, { state: navigationState });
    } else if (knowledgeBaseId) {
      // Fallback: Navigate directly to knowledge base detail page
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

  // Handle insert/edit content
  const handleInsertContent = async (_docId: string, content: string, chunkId?: string): Promise<boolean> => {
    try {
      if (chunkId) {
        // Edit mode: Update existing chunk
        const response = await updateDocumentChunk(knowledgeBaseId || '', documentId, chunkId, { content });
        
        // Update frontend list directly without waiting for backend cache refresh
        setChunkList(prev => prev.map(item => 
          item.metadata?.doc_id === chunkId 
            ? { ...item, page_content: response.page_content || content }
            : item
        ));
        
        // Edit mode returns special flag to tell InsertModal not to call onSuccess
        return true;
      } else {
        // Insert mode: Create new chunk
        await createDocumentChunk(knowledgeBaseId || '', documentId, { content });
        return true;
      }
    } catch (error) {
      console.error('Operation failed:', error);
      return false;
    }
  };

  // Handle click on text chunk
  const handleChunkClick = (item: RecallTestData, index: number) => {
    if (!documentId) return;
    const chunkId = String(item.metadata?.doc_id || index);
    insertModalRef.current?.handleOpen(documentId, item.page_content, chunkId);
  };

  // Callback after successful insert (only for inserting new chunks, edit operations are already updated synchronously in handleInsertContent)
  const handleInsertSuccess = () => {
    // Set manual refresh flag to prevent useEffect from calling repeatedly
    isManualRefreshRef.current = true;
    
    // Reset page number
    setPage(1);
    
    // Wait for backend processing to complete, then reload data (only for inserting new chunks)
    setTimeout(() => {
      ChunkList(1, false, true).then(() => {
        return fetchDocumentDetail();
      }).catch(err => {
        console.error('Refresh failed:', err);
      });
    }, 1000);
  };
  const handleAdjustmentParameter = () =>{
    if (!knowledgeBaseId || !document) return;
    const targetFileId = document.id;
    // Prioritize parentId from location, then document.parent_id, finally knowledgeBaseId
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
          {/* Document preview */}
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
      {/* Header */}
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

      {/* Content area */}
      <div className="rb:flex rb:h-full rb:gap-4 rb:flex-1 rb:overflow-hidden">
        {/* Left: Document info */}
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
        
        {/* Right: Chunk list */}
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
      
      {/* Insert content modal */}
      <InsertModal 
        ref={insertModalRef}
        onInsert={handleInsertContent}
        onSuccess={handleInsertSuccess}
      />
    </div>
  </>);
};

export default DocumentDetails;

