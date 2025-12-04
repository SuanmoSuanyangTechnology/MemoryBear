
import { useEffect, useState, useRef, type FC } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Switch, Button, Dropdown, Space, Modal, message } from 'antd';
import type { MenuProps } from 'antd';
import SearchInput from '@/components/SearchInput'
import Table, { type TableRef } from '@/components/Table'
import type { ColumnsType } from 'antd/es/table';
import type { AnyObject } from 'antd/es/_util/type';
import { MoreOutlined } from '@ant-design/icons';
import folderIcon from '@/assets/images/knowledgeBase/folder.png';
import textIcon from '@/assets/images/knowledgeBase/text.png';
import editIcon from '@/assets/images/knowledgeBase/edit.png';
import { getKnowledgeBaseDetail, deleteDocument, downloadFile, updateKnowledgeBase } from '../service';
import type { 
  CreateModalRef, 
  KnowledgeBaseListItem, 
  RecallTestDrawerRef, 
  CreateFolderModalRef, 
  CreateImageModalRef,
  ShareModalRef,
  CreateDatasetModalRef,FolderFormData, 
  KnowledgeBaseDocumentData 
} from '../types';
import RecallTestDrawer from '../components/RecallTestDrawer';
import CreateFolderModal from '../components/CreateFolderModal';
import CreateModal from '../components/CreateModal';
import ShareModal from '../components/ShareModal';
import CreateDatasetModal from '../components/CreateDatasetModal';
import CreateImageDataset from '../components/CreateImageDataset';
import FolderTree, { type TreeNodeData } from '../components/FolderTree';
import { formatDateTime } from '@/utils/format';
import { useMenu } from '@/store/menu';
import './Private.css'
const { confirm } = Modal
// 树节点数据类型

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
  const createImageDataset = useRef<CreateImageModalRef>(null)
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBaseListItem | null>(null);
  const [folder, setFolder] = useState<FolderFormData | null>({
    kb_id:knowledgeBaseId ?? '',
    parent_id:parentId ?? ''
  });
  const [query, setQuery] = useState<Record<string, unknown>>({
    orderby: 'created_at',
    desc: true,
  });
  const modalRef = useRef<CreateModalRef>(null)
  const shareModalRef = useRef<ShareModalRef>(null);
  const datasetModalRef = useRef<CreateDatasetModalRef>(null);
  const [folderTreeRefreshKey, setFolderTreeRefreshKey] = useState(0);
  const { allBreadcrumbs, setCustomBreadcrumbs } = useMenu();
  const [folderPath, setFolderPath] = useState<Array<{ id: string; name: string }>>([]);
  useEffect(() => {
    if (knowledgeBaseId) {
      let url = `/documents/${knowledgeBaseId}/${parentId}/documents`;
      setTableApi(url);
      fetchKnowledgeBaseDetail(knowledgeBaseId);
    }
  }, [knowledgeBaseId]);

  // 更新面包屑
  useEffect(() => {
    if (knowledgeBase) {
      updateBreadcrumbs();
    }
  }, [knowledgeBase, folderPath]);

  // 监听 tableApi 变化，自动刷新表格数据
  useEffect(() => {
    if (tableApi) {
      tableRef.current?.loadData();
    }
  }, [tableApi]);

  // 监听 location state 变化，如果有 refresh 标志则刷新列表
  useEffect(() => {
    const state = location.state as { refresh?: boolean; timestamp?: number } | null;
    if (state?.refresh) {
      tableRef.current?.loadData();
      // 清除 state，避免重复刷新
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, [location.state]);

  const fetchKnowledgeBaseDetail = async (id: string) => {
    setLoading(true);
    try {
      const res = await getKnowledgeBaseDetail(id);
      // 将 KnowledgeBase 转换为 KnowledgeBaseListItem
      const listItem = res as unknown as KnowledgeBaseListItem;
      setKnowledgeBase(listItem);
    } finally {
      setLoading(false);
    }
  };

  // 更新面包屑，包含知识库名称和文件夹路径
  const updateBreadcrumbs = () => {
    if (!knowledgeBase) return;
    
    const baseBreadcrumbs = allBreadcrumbs['space'] || [];
    // 只保留知识库菜单项之前的面包屑
    const knowledgeBaseMenuIndex = baseBreadcrumbs.findIndex(item => item.path === '/knowledge-base');
    const filteredBaseBreadcrumbs = knowledgeBaseMenuIndex >= 0 
      ? baseBreadcrumbs.slice(0, knowledgeBaseMenuIndex + 1)
      : baseBreadcrumbs;
    
    const customBreadcrumbs = [
      ...filteredBaseBreadcrumbs,
      {
        id: 0,
        parent: 0,
        code: null,
        label: knowledgeBase.name,
        i18nKey: null,
        path: null,
        enable: true,
        display: true,
        level: 0,
        sort: 0,
        icon: null,
        iconActive: null,
        menuDesc: null,
        deleted: null,
        updateTime: 0,
        new_: null,
        keepAlive: false,
        master: null,
        disposable: false,
        appSystem: null,
        subs: [],
      },
      ...folderPath.map((folder) => ({
        id: 0,
        parent: 0,
        code: null,
        label: folder.name,
        i18nKey: null,
        path: null,
        enable: true,
        display: true,
        level: 0,
        sort: 0,
        icon: null,
        iconActive: null,
        menuDesc: null,
        deleted: null,
        updateTime: 0,
        new_: null,
        keepAlive: false,
        master: null,
        disposable: false,
        appSystem: null,
        subs: [],
      })),
    ];

    setCustomBreadcrumbs(customBreadcrumbs, 'space');
  };

  // 处理树节点选择
  const onSelect = (selectedKeys: React.Key[]) => {
    if (!selectedKeys.length) return;
    if (!folder) return;
    const f = {
      ...folder,
      parent_id: String(selectedKeys[0]),
    }
    let url = `/documents/${knowledgeBaseId}/${String(selectedKeys[0])}/documents`;
    setTableApi(url);
    setParentId(String(selectedKeys[0]))
    setFolder(f)
  };

  // 处理文件夹路径变化
  const handleFolderPathChange = (path: Array<{ id: string; name: string }>) => {
    setFolderPath(path);
  };

  // 处理树节点展开
  const onExpand = (_expandedKeys: React.Key[], _info: any) => {
    // 展开节点时不需要特殊处理
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
      label: (<span>{t('knowledgeBase.text')} {t('knowledgeBase.dataset')}</span>),
      onClick: () => {
        datasetModalRef?.current?.handleOpen(knowledgeBase?.id,folder?.parent_id ?? knowledgeBase?.id ?? '');
      },
    },
    // 暂时未实现
    // {
    //   key: '3',
    //   icon: <img src={imageIcon} alt="image" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.imageDataSet'),
    //   onClick: () => {
    //     createImageDataset?.current?.handleOpen(knowledgeBaseId || '', parentId || '')
    //   },
    // },
    // {
    //   key: '4',
    //   icon: <img src={blankIcon} alt="blank" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.blankDataset'),
    //   onClick: () => {
    //     handleCreate('folder'); // 传入 type: 'folder'
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
    //     handleCreate('folder'); // 传入 type: 'folder'
    //   },
    // },
    // {
    //   key: '7',
    //   icon: <img src={backupIcon} alt="import" style={{ width: 16, height: 16 }} />,
    //   label: t('knowledgeBase.importBackup'),
    //   onClick: () => {
    //     handleCreate('folder'); // 传入 type: 'folder'
    //   },
    // },
    
  ];
  
  // 处理开关
  const onChange = (checked: boolean) => {
    updateKnowledgeBase(knowledgeBaseId || '', {
      status: checked ? 1 : 0,
    });
    console.log(`switch to ${checked}`);
  };
  // 处理搜索
  const handleSearch = (value?: string) => {
    setQuery({ ...query, keywords: value })
  }

  // 处理分享
  const handleShare = () => {
    shareModalRef?.current?.handleOpen(knowledgeBaseId,knowledgeBase);
  }
  // 处理分享回调，接收选中的数据
  const handleShareCallback = (selectedData: { checkedItems: any[], selectedItem: any | null }) => {
    console.log('选中的数据:', selectedData);
    // checkedItems: 所有 checked 为 true 的数据
    // selectedItem: 当前选中的项（curIndex 对应的数据）
    // 在这里处理分享逻辑
  }
  const handleCreateDatasetCallback = (payload: { value: number; title: string; description: string }) => {
    console.log('创建数据集:', payload);
  }
  // 处理设置
  const handleSetting = () => {
    modalRef?.current?.handleOpen(knowledgeBase, '');
  }
  // 处理召回测试
  const handleRecallTest = () => {
    recallTestDrawerRef?.current?.handleOpen(knowledgeBaseId);
  }

  // new / import
  const handelCreateOrImport = () => {

  }
  // 生成下拉菜单项（根据当前 row）
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
              // 刷新表格数据
              tableRef.current?.loadData();
            })
            .catch((err: any) => {
              console.log('删除失败', err);
            });
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
    // 刷新列表数据
  if (loading) {
    return <div>加载中...</div>;
  }

  if (!knowledgeBase) {
    return <div>知识库不存在</div>;
  }
  const refreshDirectoryTree = async () => {
    // 先刷新知识库详情，确保数据是最新的
    await fetchKnowledgeBaseDetail(knowledgeBase.id);
    // 添加短暂延迟，确保后端数据已经完全更新
    await new Promise(resolve => setTimeout(resolve, 300));
     // 然后刷新文件夹树
    setFolderTreeRefreshKey((prev) => prev + 1);
    if (!folder) {
      setFolder({
        kb_id: knowledgeBaseId ?? '',
        parent_id: parentId ?? knowledgeBaseId ?? ''
      });
    }
   
  }
  const handleRootTreeLoad = (nodes: TreeNodeData[] | null) => {
    if (!nodes || nodes.length === 0) {
      setFolder(null);
    } else {
      // 如果有节点且 folder 为 null，重新设置 folder
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
    // 刷新表格数据
    tableRef.current?.loadData();
  }
  
  return (
    <>
    {contextHolder}
    <div className="rb:flex rb:h-full rb:gap-4">
      {folder && (
        <div className="rb:w-80 rb:flex-shrink-0 rb:h-[calc(100%+40px)] rb:mt-[-16px] rb:border-r rb:border-[#EAECEE] rb:p-4 rb:bg-transparent">
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
            <Button onClick={handleShare}>{t('knowledgeBase.share')}</Button>
            <Button onClick={handleRecallTest}>{t('knowledgeBase.recallTest')}</Button>
            <Button onClick={handleSetting}>{t('knowledgeBase.knowledgeBase')} {t('knowledgeBase.setting')}</Button>
            <Dropdown menu={{ items: createItems }} trigger={['click']}>
                <Button type="primary" onClick={handelCreateOrImport} >+ {t('knowledgeBase.createImport')}</Button>
            </Dropdown>
            
          </div>
        </div>
        <div className="rb:rounded rb:max-h-[calc(100%-100px)] rb:overflow-y-auto">
          <Table
            ref={tableRef}
            apiUrl={tableApi}
            apiParams={query as Record<string, unknown>}
            columns={columns}
            rowKey="id"
            scrollX={1500}
          />
        </div>
      </div>
      <RecallTestDrawer 
        ref={recallTestDrawerRef}
      />
      <CreateFolderModal
        ref={createFolderModalRef}
        refreshTable={refreshDirectoryTree}
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

