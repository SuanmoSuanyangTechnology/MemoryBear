
import { useEffect, useState, useRef, useCallback, type FC } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Switch, Button, Dropdown, Space, Modal, message, Radio, Tooltip } from 'antd';
import type { MenuProps } from 'antd';
import SearchInput from '@/components/SearchInput'
import Table, { type TableRef } from '@/components/Table'
import type { ColumnsType } from 'antd/es/table';
import type { AnyObject } from 'antd/es/_util/type';
import { MoreOutlined, DeploymentUnitOutlined, BarsOutlined } from '@ant-design/icons';
import folderIcon from '@/assets/images/knowledgeBase/folder.png';
import textIcon from '@/assets/images/knowledgeBase/text.png';
import editIcon from '@/assets/images/knowledgeBase/edit.png';
// import blankIcon from '@/assets/images/knowledgeBase/blankDocument.png';
// import imageIcon from '@/assets/images/knowledgeBase/image.png'
import { getKnowledgeBaseDetail, deleteDocument, downloadFile, updateKnowledgeBase } from '@/api/knowledgeBase';
import { 
  type CreateModalRef, 
  type KnowledgeBaseListItem, 
  type RecallTestDrawerRef, 
  type CreateFolderModalRef, 
  type CreateSetModalRef,
  type ShareModalRef,
  type CreateDatasetModalRef,
  type FolderFormData, 
  type KnowledgeBaseDocumentData,
  type KnowledgeBaseFormData,
} from '@/views/KnowledgeBase/types';
import RecallTestDrawer from '../components/RecallTestDrawer';
import CreateFolderModal from '../components/CreateFolderModal';
import CreateContentModal from '../components/CreateContentModal';
import CreateModal from '../components/CreateModal';
import ShareModal from '../components/ShareModal';
import CreateDatasetModal from '../components/CreateDatasetModal';
import CreateImageDataset from '../components/CreateImageDataset';
import FolderTree, { type TreeNodeData } from '../components/FolderTree';
import { formatDateTime } from '@/utils/format';
import KnowledgeGraphCard from '../components/KnowledgeGraphCard';
import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import './Private.css'
const { confirm } = Modal
// Tree node data type

const Private: FC = () => {
  const { t } = useTranslation();
  const [messageApi, contextHolder] = message.useMessage();
  const navigate = useNavigate();
  const location = useLocation();
  const { knowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const [parentId, setParentId] = useState<string | undefined>(knowledgeBaseId);
  const [loading, setLoading] = useState(false);
  const tableRef = useRef<TableRef>(null);
  const [tableApi, setTableApi] = useState<string | undefined>(undefined);
  const recallTestDrawerRef = useRef<RecallTestDrawerRef>(null);
  const createFolderModalRef = useRef<CreateFolderModalRef>(null);
  const createImageDataset = useRef<CreateSetModalRef>(null)
  const createContentModalRef = useRef<CreateSetModalRef>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBaseListItem | null>(null);
  const [folder, setFolder] = useState<FolderFormData | null>({
    kb_id:knowledgeBaseId ?? '',
    parent_id:parentId ?? ''
  });
  const [query, setQuery] = useState<Record<string, unknown>>({
    orderby: 'created_at',
    desc: true
  });
  const modalRef = useRef<CreateModalRef>(null)
  const shareModalRef = useRef<ShareModalRef>(null);
  const datasetModalRef = useRef<CreateDatasetModalRef>(null);
  const [folderTreeRefreshKey, setFolderTreeRefreshKey] = useState(0);
  const [autoExpandPath, setAutoExpandPath] = useState<Array<{ id: string; name: string }>>([]);
  const [isGraph, setIsGraph] = useState(false);
  const { updateBreadcrumbs } = useBreadcrumbManager({
    breadcrumbType: 'detail',
    // Don't provide onKnowledgeBaseMenuClick, let it use default navigation behavior (return to list page)
    onKnowledgeBaseFolderClick: useCallback((folderId: string, folderPath: Array<{ id: string; name: string }>) => {
      // Navigate to corresponding folder when clicking folder breadcrumb
      setParentId(folderId);
      setFolderPath(folderPath);
      setSelectedKeys([folderId]);
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: folderId
      });
      
      // Ensure query object changes to trigger table refresh
      setQuery({
        orderby: 'created_at',
        desc: true,
        parent_id: folderId,
        _timestamp: Date.now()
      });
      
      // Ensure API URL is set correctly
      setTableApi(`/documents/${knowledgeBaseId}/documents`);
      
      // Manually trigger table refresh to ensure data update
      setTimeout(() => {
        tableRef.current?.loadData();
      }, 100);
    }, [knowledgeBaseId])
  });
  const [folderPath, setFolderPath] = useState<BreadcrumbItem[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([]);
  const [knowledgeBaseFolderPath, setKnowledgeBaseFolderPath] = useState<BreadcrumbItem[]>([]);
  const fetchKnowledgeBaseDetail = async (id: string) => {
    setLoading(true);
    try {
      const res = await getKnowledgeBaseDetail(id);
      // Convert KnowledgeBase to KnowledgeBaseListItem
      const listItem = res as unknown as KnowledgeBaseListItem;
      setKnowledgeBase(listItem);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (knowledgeBaseId) {
      let url = `/documents/${knowledgeBaseId}/documents`;
      setTableApi(url);
      fetchKnowledgeBaseDetail(knowledgeBaseId);
      
      // Immediately set base breadcrumbs to ensure other page breadcrumbs are not displayed
      updateBreadcrumbs({
        knowledgeBaseFolderPath,
        knowledgeBase: {
          id: knowledgeBaseId,
          name: '加载中...',
          type: 'knowledgeBase'
        },
        documentFolderPath: folderPath,
      });
    }
  }, [knowledgeBaseId]);

  // Update breadcrumbs
  useEffect(() => {
    if (knowledgeBase) {
      updateBreadcrumbs({
        knowledgeBaseFolderPath,
        knowledgeBase: {
          id: knowledgeBase.id,
          name: knowledgeBase.name,
          type: 'knowledgeBase'
        },
        documentFolderPath: folderPath,
      });
    }
  }, [knowledgeBase, knowledgeBaseFolderPath, folderPath, updateBreadcrumbs]);

  // Listen to tableApi changes and auto refresh table data
  useEffect(() => {
    if (tableApi) {
      tableRef.current?.loadData();
    }
  }, [tableApi]);

  // Listen to query changes and ensure table data update
  useEffect(() => {
    if (tableApi && query._timestamp) {
      // When query has _timestamp, it means the update is triggered by breadcrumb or other means
      tableRef.current?.loadData();
    }
  }, [query._timestamp, tableApi]);

  // Listen to location state changes
  useEffect(() => {
    const state = location.state as { 
      refresh?: boolean; 
      timestamp?: number;
      fromKnowledgeBaseList?: boolean;
      knowledgeBaseFolderPath?: BreadcrumbItem[];
      parentId?: string;
      navigateToDocumentFolder?: string;
      documentFolderPath?: BreadcrumbItem[];
      resetToRoot?: boolean;
    } | null;
    
    if (state?.refresh) {
      tableRef.current?.loadData();
      // Clear state to avoid repeated refresh
      navigate(location.pathname, { replace: true, state: {} });
    }
    
    // If navigated from knowledge base list page, set knowledge base folder path
    if (state?.fromKnowledgeBaseList && state?.knowledgeBaseFolderPath) {
      setKnowledgeBaseFolderPath(state.knowledgeBaseFolderPath);
    }
    
    // If need to reset to root directory (return to initial state)
    if (state?.resetToRoot) {
      // Reset all states to initial state, consistent with page initialization
      setParentId(knowledgeBaseId);
      setFolderPath([]);
      setSelectedKeys([]);
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: knowledgeBaseId ?? ''
      });
      setQuery({
        orderby: 'created_at',
        desc: true,
        _timestamp: Date.now() // Add timestamp to ensure query object changes and trigger API call
      });
      
      // Reset API URL
      const rootUrl = `/documents/${knowledgeBaseId}/documents`;
      setTableApi(rootUrl);
      
      // Clear auto expand path
      setAutoExpandPath([]);
      
      // Refresh folder tree - use delay to ensure state reset is complete before refresh
      setTimeout(() => {
        setFolderTreeRefreshKey((prev) => prev + 1);
      }, 100);
      
      // Manually trigger table refresh to ensure data update
      setTimeout(() => {
        tableRef.current?.loadData();
      }, 200);
      
      // Clear state to avoid repeated processing
      navigate(location.pathname, { replace: true, state: {} });
    }
    
    // If returning from document details page, restore document folder path
    if (state?.navigateToDocumentFolder && state?.documentFolderPath) {
      setFolderPath(state.documentFolderPath);
      setParentId(state.navigateToDocumentFolder);
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: state.navigateToDocumentFolder
      });
      setQuery(prevQuery => ({
        ...prevQuery,
        parent_id: state.navigateToDocumentFolder,
        _timestamp: Date.now()
      }));
      setTableApi(`/documents/${knowledgeBaseId}/documents`);
      setSelectedKeys([state.navigateToDocumentFolder]);
      
      // Set auto expand path to let FolderTree auto expand to corresponding position
      setAutoExpandPath(state.documentFolderPath);
      
      // Manually trigger table refresh
      setTimeout(() => {
        tableRef.current?.loadData();
      }, 100);
      
      // Clear auto expand path to avoid repeated trigger (delayed clear to ensure FolderTree processing is complete)
      setTimeout(() => {
        setAutoExpandPath([]);
      }, 2000);
    }
  }, [location.state, knowledgeBaseId, navigate, location.pathname]);

  // Handle tree node selection
  const onSelect = (keys: React.Key[]) => {
    if (!keys.length) {
      // If no node is selected, return to root directory (initial state)
      setParentId(knowledgeBaseId);
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: knowledgeBaseId ?? ''
      });
      setQuery({
        orderby: 'created_at',
        desc: true,
        _timestamp: Date.now() // Add timestamp to ensure query object changes
      });
      setSelectedKeys([]);
      return;
    }
    
    if (!folder) return;
    
    const f = {
      ...folder,
      parent_id: String(keys[0]),
    }
    setQuery({
      ...query,
      parent_id: String(keys[0]),
      _timestamp: Date.now() // Add timestamp to ensure query object changes
    })
    let url = `/documents/${knowledgeBaseId}/documents`;
    
    setTableApi(url);
    setParentId(String(keys[0]))
    setFolder(f)
    setSelectedKeys(keys)
  };

  // Handle folder path change
  const handleFolderPathChange = (path: Array<{ id: string; name: string }>) => {
    setFolderPath(path);
  };

  // Handle tree node expand
  const onExpand = (_expandedKeys: React.Key[], _info: any) => {
    // No special handling needed when expanding nodes
  };
  // create / import list
  const createItems: MenuProps['items'] = [
    {
      key: '1',
      icon: <img src={folderIcon} alt="dataset" style={{ width: 16, height: 16 }} />,
      label: t('knowledgeBase.folder'),
      onClick: () => {
        let f: FolderFormData | null = null;
        f = {
          kb_id: knowledgeBase?.id ?? '',
          parent_id:folder?.parent_id ?? knowledgeBase?.id ?? '',
        }
          // setFolder(f);
        
        createFolderModalRef?.current?.handleOpen(f as FolderFormData);
      },
    },
    {
      key: '2',
      icon: <img src={textIcon} alt="text" style={{ width: 16, height: 16 }} />,
      label: (<span>{t('knowledgeBase.createA')} {t('knowledgeBase.dataset')}</span>),
      onClick: () => {
        datasetModalRef?.current?.handleOpen(knowledgeBase?.id,folder?.parent_id ?? knowledgeBase?.id ?? '');
      },
    },
    // {
    //   key: '8',
    //   icon: <img src={blankIcon} alt="Custome Text" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.mediaDataSet'),
    //   onClick: () => {
    //     createContentModalRef?.current?.handleOpen(knowledgeBase?.id ?? '', folder?.parent_id ?? knowledgeBase?.id ?? '');
    //   },
    // },
    // {
    //   key: '3',
    //   icon: <img src={imageIcon} alt="image" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.imageDataSet'),
    //   onClick: () => {
    //     createImageDataset?.current?.handleOpen(knowledgeBaseId || '', parentId || '')
    //   },
    // },
        // Not implemented yet
    // {
    //   key: '4',
    //   icon: <img src={blankIcon} alt="blank" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.blankDataset'),
    //   onClick: () => {
    //     handleCreate('folder'); // Pass type: 'folder'
    //   },
    // },
    // {
    //   key: '5',
    //   type: 'divider',
    // },
    // {
    //   key: '6',
    //   icon: <img src={templateIcon} alt="import" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.importTemplate'),
    //   onClick: () => {
    //     handleCreate('folder'); // Pass type: 'folder'
    //   },
    // },
    // {
    //   key: '7',
    //   icon: <img src={backupIcon} alt="import" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.importBackup'),
    //   onClick: () => {
    //     handleCreate('folder'); // Pass type: 'folder'
    //   },
    // },
    
  ];
  
  // Handle switch
  const onChange = (checked: boolean) => {
    if (!knowledgeBase) return;
    
    // Construct complete update data, keeping existing configuration
    const updateData: KnowledgeBaseFormData = {
      name: knowledgeBase.name,
      description: knowledgeBase.description,
      embedding_id: knowledgeBase.embedding_id,
      llm_id: knowledgeBase.llm_id,
      image2text_id: knowledgeBase.image2text_id,
      reranker_id: knowledgeBase.reranker_id,
      permission_id: knowledgeBase.permission_id,
      type: knowledgeBase.type,
      status: checked ? 1 : 0,
      parser_config: knowledgeBase.parser_config || {
        chunk_token_num: 512,
        delimiter: '\n',
        auto_keywords: 0,
        auto_questions: 0,
        html4excel: false,
        graphrag: {
          use_graphrag: false,
          scene_name: '',
          entity_types: [],
          method: '',
          resolution: false,
          community: false
        }
      }
    };
    
    updateKnowledgeBase(knowledgeBaseId || '', updateData);
    console.log(`switch to ${checked}`);
  };
  // Handle search
  const handleSearch = (value?: string) => {
    setQuery({ ...query, keywords: value })
  }

  // Handle share
  const handleShare = () => {
    shareModalRef?.current?.handleOpen(knowledgeBaseId,knowledgeBase);
  }
  // Handle share callback, receive selected data
  const handleShareCallback = (selectedData: { checkedItems: any[], selectedItem: any | null }) => {
    console.log('Selected data:', selectedData);
    // checkedItems: All data with checked = true
    // selectedItem: Currently selected item (corresponding to curIndex)
    // Handle share logic here
  }
  const handleCreateDatasetCallback = (payload: { value: number; title: string; description: string }) => {
    console.log('Create dataset:', payload);
  }
  // Handle settings
  const handleSetting = () => {
    modalRef?.current?.handleOpen(knowledgeBase, '');
  }
  // Handle recall test
  const handleRecallTest = () => {
    recallTestDrawerRef?.current?.handleOpen(knowledgeBaseId);
  }

  // new / import
  const handelCreateOrImport = () => {

  }
  // Generate dropdown menu items (based on current row)
  const getOptMenuItems = (row: KnowledgeBaseListItem): MenuProps['items'] => [
    {
      key: '1',
      label: t('knowledgeBase.rechunking'),
      onClick: () => {
        handleRechunking(row);
      },
    },
    {
      key: '2',
      label: t('knowledgeBase.download'),
      onClick: () => {
        handleDownload(row);
      },
    },
    {
      key: '3',
      label: t('knowledgeBase.delete'),
      onClick: () => {
        handleDelete(row);
      },
    }
  ];
  const handleRechunking = (item: KnowledgeBaseListItem) => {
    if (!knowledgeBaseId) return;
    const document = item as unknown as KnowledgeBaseDocumentData;
    const targetFileId =  document?.id || document?.file_id;
    navigate(`/knowledge-base/${knowledgeBaseId}/create-dataset`, {
      state: {
        source: 'local',
        knowledgeBaseId,
        parentId: parentId ?? knowledgeBaseId,
        startStep: 'parameterSettings',
        fileId: targetFileId,
      },
    });
  }
  const handleDownload = (item: KnowledgeBaseListItem) => {
    const document = item as unknown as KnowledgeBaseDocumentData;
    const targetFileId =  document?.file_id ?? '';
    const fileName = document?.file_name ?? '';
    downloadFile(targetFileId, fileName);
  }
  const handleDelete = (item: any) => {
      confirm({
        title: t('common.deleteWarning'),
        content: t('common.deleteWarningContent', { content: item.file_name }),
        onOk: () => {
          deleteDocument(item.id)
            .then(() => {
              messageApi.success(t('common.deleteSuccess'));
              // Refresh table data
              tableRef.current?.loadData();
            })
            .catch((err: any) => {
              console.log('Delete failed', err);
            });
        },
        onCancel: () => {
          console.log('Cancel delete');
        },
      });
  }
  // Table column configuration
  const columns: ColumnsType = [
    {
      title: t('knowledgeBase.name'),
      dataIndex: 'file_name',
      key: 'file_name',
      render: (text: string, record: AnyObject) => {
        const document = record as KnowledgeBaseDocumentData;
        return (
          <span
            className="rb:text-blue-600 rb:cursor-pointer rb:hover:underline"
            onClick={() => {
              if (knowledgeBaseId && document.id) {
                navigate(`/knowledge-base/${knowledgeBaseId}/DocumentDetails`,{
                  state: {
                    documentId: document.id,
                    parentId: parentId ?? knowledgeBaseId,
                    // Pass breadcrumb information
                    breadcrumbPath: {
                      knowledgeBaseFolderPath,
                      knowledgeBase: {
                        id: knowledgeBase?.id || knowledgeBaseId,
                        name: knowledgeBase?.name || '',
                        type: 'knowledgeBase'
                      },
                      documentFolderPath: folderPath,
                      document: {
                        id: document.id,
                        name: document.file_name || '',
                        type: 'document'
                      }
                    }
                  },
                });
              }
            }}
          >
            {text}
          </span>
        );
      },
    },
    {
      title: t('knowledgeBase.status'),
      dataIndex: 'progress',
      key: 'progress',
      render: (value: string | number) => {
        return (
          <span className="rb:text-xs rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded rb:items-center rb:text-[#212332] rb:py-1 rb:px-2">
            <span
              className="rb:inline-block rb:w-[5px] rb:h-[5px] rb:mr-2 rb:rounded-full"
              style={{ backgroundColor: value === 1 ? '#369F21' : value === 0 ? '#FF0000' : '#FF8A4C' }}
            ></span>
            <span>{value === 1 ? t('knowledgeBase.completed') : value === 0 ? t('knowledgeBase.pending') : t('knowledgeBase.processing')}</span>
          </span>
        );
      }
    },{
      title: t('knowledgeBase.processMsg'),
      dataIndex: 'progress_msg',
      key: 'progress_msg',
      width: 320,
      render: (value: string) => {
        if (!value) return '-';
        
        // Parse log format, convert \n to newline
        const formattedText = value.replace(/\\n/g, '\n');
        
        return (
          <Tooltip title={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{formattedText}</pre>} placement="topLeft">
            <div 
              style={{
                maxWidth: '320px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                lineHeight: '1.5',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word'
              }}
            >
              {formattedText}
            </div>
          </Tooltip>
        );
      }
    },
    {
      title: t('knowledgeBase.processingMode'),
      dataIndex: 'parser_id',
      key: 'parser_id',
    },
    {
      title: t('knowledgeBase.dataSize'),
      dataIndex: 'file_size',
      key: 'file_size',
    },
    {
      title: t('knowledgeBase.createUpdateTime'),
      dataIndex: 'created_at',
      key: 'created_at',
      render:(value:string) => {
        return(
          <span>{formatDateTime(value,'YYYY-MM-DD HH:mm:ss')}</span>
        )
      }
    },

    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      width: 100,
      render: (_, record) => (
        <Space size="middle">
          <Dropdown
            menu={{ items: getOptMenuItems(record as KnowledgeBaseListItem) }}
            trigger={['click']}
          >
              <MoreOutlined className='rb:text-base rb:font-semibold'/>
          </Dropdown>
        </Space>
      ),
    },
  ];
    // Refresh list data
  if (loading) {
    return <div>Loading...</div>;
  }

  if (!knowledgeBase) {
    return <div>知识库不存在</div>;
  }
  const refreshDirectoryTree = async () => {
    // First refresh knowledge base details to ensure data is up-to-date
    if (knowledgeBase?.id) {
      await fetchKnowledgeBaseDetail(knowledgeBase.id);
    }
    // Add short delay to ensure backend data is fully updated
    await new Promise(resolve => setTimeout(resolve, 300));
     // Then refresh folder tree
    setFolderTreeRefreshKey((prev) => prev + 1);
    
    // Ensure folder state is set correctly
    if (!folder) {
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: parentId ?? knowledgeBaseId ?? ''
      });
    }
   
  }
  const handleRootTreeLoad = (nodes: TreeNodeData[] | null) => {
    if (!nodes || nodes.length === 0) {
      // If no nodes, set folder to null (this will hide FolderTree)
      setFolder(null);
    } else {
      // If there are nodes and folder is null, reset folder
      if (!folder) {
        setFolder({
          kb_id: knowledgeBaseId ?? '',
          parent_id: parentId ?? knowledgeBaseId ?? ''
        });
      }
    }
  };
  const handleEditFolder = () => {
    const f = {
      id:knowledgeBase.id,
      parent_id:knowledgeBase.parent_id,
      kb_id:knowledgeBase.id,
      folder_name:knowledgeBase.name
    }
    // setFolder(f)
    createFolderModalRef?.current?.handleOpen(f,'edit');
  }

  const handleRefreshTable = () => {
    // Refresh table data
    fetchKnowledgeBaseDetail(knowledgeBase.id)
    tableRef.current?.loadData();
  }
  return (
    <>
    {contextHolder}
    <div className="rb:flex rb:h-full rb:gap-4">
      {folder && (
        <div className="rb:w-64 rb:flex-shrink-0 rb:h-[calc(100%+40px)] rb:mt-[-16px] rb:border-r rb:border-[#EAECEE] rb:p-4 rb:bg-transparent">
            <FolderTree
              multiple
              className="customTree"
              style={{ background: 'transparent' }}
              onSelect={onSelect}
              onExpand={onExpand}
              knowledgeBaseId={knowledgeBaseId ?? ''}
              refreshKey={folderTreeRefreshKey}
              onRootLoad={handleRootTreeLoad}
              onFolderPathChange={handleFolderPathChange}
              selectedKeys={selectedKeys}
              autoExpandPath={autoExpandPath}
            />
        </div>
      )}
      <div className='rb:flex-1 rb:min-w-0'>
        <div className="rb:flex rb:items-center rb:justify-between rb:mb-4">
          
          <div className="rb:flex-col">
            <div className="rb:flex rb:items-center rb:gap-3">
                <h1 className="rb:text-xl rb:font-medium rb:text-gray-800">{knowledgeBase.name}</h1>
                <div className="rb:flex rb:items-center rb:border rb:border-[rgba(33, 35, 50, 0.17)] rb:text-gray-500 rb:cursor-pointer rb:px-1 rb:py-0.5 rb:rounded"
                  onClick={handleEditFolder}
                >
                  <img src={editIcon} alt="edit" className="rb:w-[14px] rb:h-[14px" />
                  <span className='rb:text-[12px]'>{t('knowledgeBase.edit')} {t('knowledgeBase.name')}</span>
                </div>
            </div>
            <div className='rb:flex rb:items-center rb:gap-6 rb:text-gray-500 rb:mt-2 rb:text-xs'>
                <span className='rb:text-[12px]'>{t('knowledgeBase.created')} {t('knowledgeBase.time')}: {formatDateTime(knowledgeBase.created_at) || '-'}</span>
                <span className='rb:text-[12px]'>{t('knowledgeBase.updated')} {t('knowledgeBase.time')}: {formatDateTime(knowledgeBase.updated_at) || '-'}</span>
                
            </div>
          </div>
          {/* <div className='rb:flex'> */}
            <Switch checkedChildren={t('common.enable')} unCheckedChildren={t('common.disable')} defaultChecked={knowledgeBase.status === 1} onChange={onChange}/>
          {/* </div> */}
        </div>
        <div className='rb:flex rb:items-center rb:justify-between rb:mb-4'>
          <SearchInput placeholder={t('knowledgeBase.search')} onSearch={handleSearch} />
          <div className='rb:flex-1 rb:flex rb:items-center rb:justify-end rb:gap-2.5'>
            <Radio.Group value={isGraph} onChange={(e) => setIsGraph(e.target.value)}>
              <Radio.Button value={false} >
                  <BarsOutlined />
              </Radio.Button>
              <Radio.Button value={true} >
                  <DeploymentUnitOutlined />
              </Radio.Button>
            </Radio.Group>
            <Button onClick={handleShare}>{t('knowledgeBase.share')}</Button>
            <Button onClick={handleRecallTest}>{t('knowledgeBase.recallTest')}</Button>
            <Button onClick={handleSetting}>{t('knowledgeBase.knowledgeBase')} {t('knowledgeBase.setting')}</Button>
            <Dropdown menu={{ items: createItems }} trigger={['click']}>
                <Button type="primary" onClick={handelCreateOrImport} >+ {t('knowledgeBase.createImport')}</Button>
            </Dropdown>
            
          </div>
        </div>
        <div className="rb:rounded rb:max-h-[calc(100%-100px)] rb:overflow-y-auto">
          {isGraph ? (
            <KnowledgeGraphCard 
              knowledgeBase={knowledgeBase} 
              onRebuildGraph={() => modalRef.current?.handleOpen(knowledgeBase, 'rebuild')}
            />
          ) : (
            <Table
              ref={tableRef}
              apiUrl={tableApi}
              apiParams={query as Record<string, unknown>}
              columns={columns}
              rowKey="id"
              scrollX={1500}
            />
          )}
        </div>
      </div>
      <RecallTestDrawer 
        ref={recallTestDrawerRef}
      />
      <CreateFolderModal
        ref={createFolderModalRef}
        refreshTable={refreshDirectoryTree}
      />
      <CreateContentModal
        ref={createContentModalRef}
        refreshTable={handleRefreshTable}
      />
      <CreateModal
        ref={modalRef}
        refreshTable={handleRefreshTable}
      />
      <ShareModal
        ref={shareModalRef}
        handleShare={handleShareCallback}
      />
      <CreateDatasetModal
        ref={datasetModalRef}
        handleCreateDataset={handleCreateDatasetCallback}
      />
      <CreateImageDataset
        ref={createImageDataset}
        refreshTable={refreshDirectoryTree}
      />
    </div>
    </>
  );
};

export default Private;

