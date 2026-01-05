import { useEffect, useState, useRef, useMemo, useCallback, type FC } from 'react';
import { Row, Col, Button, Dropdown, Modal, message, Tooltip } from 'antd'
import type { MenuProps } from 'antd';
import { EllipsisOutlined } from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';

import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import folderIcon from '@/assets/images/knowledgeBase/folder.png';
import generalIcon from '@/assets/images/knowledgeBase/datasets.png';
import webIcon from '@/assets/images/knowledgeBase/general.png';
import tpIcon from '@/assets/images/knowledgeBase/text.png';
import type { KnowledgeBaseListItem, CreateModalRef, KnowledgeBaseListResponse, ListQuery } from '@/views/KnowledgeBase/types'
import CreateModal from './components/CreateModal'
import RbCard from '@/components/RbCard'
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'
import { getKnowledgeBaseList, getModelList, getModelTypeList, deleteKnowledgeBase, getKnowledgeBaseTypeList } from '@/api/knowledgeBase'
const { confirm } = Modal;
import InfiniteScroll from 'react-infinite-scroll-component';

import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';

type ModelMenuInfo = {
  menu: NonNullable<MenuProps['items']>;
  summary: string[];
};

const KnowledgeBaseManagement: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<KnowledgeBaseListItem[]>([])
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [query, setQuery] = useState<ListQuery>({
    orderby:'created_at',
    desc:true,
  })
  const [modelTypes, setModelTypes] = useState<string[]>([]);
  const [modelMenus, setModelMenus] = useState<Record<string, ModelMenuInfo>>({});
  const [knowledgeBaseTypes, setKnowledgeBaseTypes] = useState<string[]>([]);
  const modelListCache = useRef<Record<string, string>>({});
  const modalRef = useRef<CreateModalRef>(null)
  const [messageApi, contextHolder] = message.useMessage();
  const processedStateRef = useRef<any>(null);
  
  // 使用面包屑管理 Hook
  const { updateBreadcrumbs } = useBreadcrumbManager({
    breadcrumbType: 'list',
    onKnowledgeBaseMenuClick: useCallback(() => {
      // 返回根目录
      setFolderPath([]);
      setQuery((prev) => ({
        ...prev,
        parent_id: undefined,
      }));
    }, []),
    onKnowledgeBaseFolderClick: useCallback((folderId: string, folderPath: Array<{ id: string; name: string }>) => {
      // 直接更新文件夹路径和查询状态
      setFolderPath(folderPath);
      setQuery((prev) => ({
        ...prev,
        parent_id: folderId,
      }));
    }, [])
  });
  const [folderPath, setFolderPath] = useState<BreadcrumbItem[]>([]);
  

  // 生成下拉菜单项（根据当前 item）
  const getOptMenuItems = (item: KnowledgeBaseListItem): MenuProps['items'] => {
    const items: NonNullable<MenuProps['items']> = [];

    // 当权限为 share 时，不显示编辑按钮
    const permissionId = (item.permission_id || '').toLowerCase();
    if (permissionId !== 'share') {
      items.push({
        key: '1',
        label: t('knowledgeBase.edit'),
        onClick: () => {
          handleEdit(item);
        },
      });
    }

    items.push({
      key: '2',
      label: t('knowledgeBase.delete'),
      onClick: () => {
        handleDelete(item);
      },
    });

    return items;
  };
  // 根据类型获取图标
  const getTypeIcon = (type: string) => {
    const normalized = (type || '').toLowerCase();
    switch (normalized) {
      case 'general':
        return generalIcon;
      case 'folder':
        return folderIcon;
      case 'web':
        return webIcon;
      case 'third-party':
      case 'tp':
        return tpIcon;
      default:
        return generalIcon;
    }
  };

  // 根据类型获取翻译 key
  const getTypeLabelKey = (type: string) => {
    const normalized = (type || '').toLowerCase();
    switch (normalized) {
      case 'general':
        return 'knowledgeBase.general';
      case 'folder':
        return 'knowledgeBase.folder';
      case 'web':
        return 'knowledgeBase.web';
      case 'third-party':
      case 'tp':
        return 'knowledgeBase.tp';
      default:
        return `knowledgeBase.${normalized}`;
    }
  };

  // 处理创建
  const handleCreate = useCallback((type?: string) => {
    // 如果在文件夹内，使用 folderPath 的最后一项作为 parent_id
    // 这样更可靠，因为 folderPath 是直接管理的状态
    const currentParentId = folderPath.length > 0 
      ? folderPath[folderPath.length - 1].id 
      : query.parent_id; // 降级使用 query.parent_id
    
    const record = currentParentId ? {
      parent_id: currentParentId as string,
    } as KnowledgeBaseListItem : null;
    
    console.log('handleCreate called:', {
      type,
      folderPath,
      folderPathLength: folderPath.length,
      queryParentId: query.parent_id,
      currentParentId,
      record
    });
    
    modalRef?.current?.handleOpen(record, type)
  }, [folderPath, query.parent_id])

  // 动态生成 createItems
  const createItems: MenuProps['items'] = useMemo(() => {
    return knowledgeBaseTypes.map((type, index) => ({
      key: String(index + 1),
      icon: <img src={getTypeIcon(type)} alt={type} style={{ width: 16, height: 16 }} />,
      label: t(getTypeLabelKey(type.toLocaleLowerCase())),
      onClick: () => {
        handleCreate(type);
      },
    }));
  }, [knowledgeBaseTypes, t, handleCreate]);
  const typeToFieldKey = (type: string) => {
    const normalized = (type || '').toLowerCase();
    switch (normalized) {
      case 'embedding':
        return 'embedding_id';
      case 'llm':
        return 'llm_id';
      case 'image2text':
        return 'image2text_id';
      case 'rerank':
      case 'reranker':
        return 'reranker_id';
      case 'chat':
        return 'chat_id';
      default:
        return `${normalized}_id`;
    }
  };
  const formatData = (data: KnowledgeBaseListItem) => {
    const keys: (keyof KnowledgeBaseListItem)[] = ['type', 'permission_id']
    return keys.map(key => ({
      key,
      label: t(`knowledgeBase.${key}`),
      children: key === 'permission_id' 
        ? ((data[key] || '').toLowerCase() === 'private' ? t('knowledgeBase.private') : t('knowledgeBase.share'))
        : String(data[key] || '-'),
    }))
  }
  const fetchModelTypes = async () => {
    try {
      const response = await getModelTypeList();
      setModelTypes(Array.isArray(response) ? [...response.filter(type => type !== 'chat'),'image2text'] : []);
    } catch (error) {
      console.error('Failed to fetch model types:', error);
      setModelTypes([]);
    }
  };
  const fetchModelList = async () => { 
    try {
      const response = await getModelList(['llm', 'embedding', 'rerank', 'chat'], { page: 1, pagesize: 100 });
      // 缓存模型列表，建立 id -> name 的映射
      if (response?.items && Array.isArray(response.items)) {
        const cache: Record<string, string> = {};
        response.items.forEach((model: any) => {
          if (model.id && model.name) {
            cache[model.id] = model.name;
          }
        });
        modelListCache.current = cache;
      }
    } catch (error) {
      console.error('Failed to fetch model list:', error);
    }
  };
  const fetchKnowledgeBaseTypes = async () => {
    try {
      let types = await getKnowledgeBaseTypeList();
      types = types.filter(type => (type === 'General' || type === 'Folder' )); //
      //暂时未实现 ，过滤掉未实现
      setKnowledgeBaseTypes(types);
    } catch (error) {
      console.error('Failed to fetch knowledge base types:', error);
      setKnowledgeBaseTypes([]);
    }
  };
  const getModelNameById = (id?: string | null) => {
    if (!id) return '';
    // 从模型列表缓存中获取模型名称
    return modelListCache.current[id] || '';
  };
  const buildModelMenuForItem = (item: KnowledgeBaseListItem): ModelMenuInfo | null => {
    const entries: { menuItem: NonNullable<MenuProps['items']>[number]; summary: string }[] = [];
    const record = item as unknown as Record<string, unknown>;
    for (const type of modelTypes) {
      const curType = type === 'rerank' ? 'reranker' : type;
      const fieldKey = typeToFieldKey(curType);
      const modelId = record[fieldKey] as string | undefined;
      if (!modelId) continue;
      const modelName = getModelNameById(modelId);
      if (!modelName) continue;
      const typeLabel = t(`knowledgeBase.createForm.${fieldKey}`) || t(`knowledgeBase.${fieldKey}`) || type;
      entries.push({
        menuItem: {
          key: `${fieldKey}_${modelId}`,
          label: (
            <span className="rb:text-gray-500 rb:text-[12px]">
              {typeLabel}: {modelName}
            </span>
          ),
        },
        summary: `${typeLabel}: ${modelName}`,
      });
    }
    if (!entries.length) {
      return null;
    }
    const header: NonNullable<MenuProps['items']>[number] = {
      key: 'header',
      label: (<span className='rb:font-medium'>{t('knowledgeBase.allModels')}</span>),
      disabled: true,
    };
    const menuArray = [header, ...entries.map(({ menuItem }) => menuItem)] as NonNullable<MenuProps['items']>;
    return {
      menu: menuArray,
      summary: entries.map(({ summary }) => summary),
    };
  };
  const buildModelMenus = (items: KnowledgeBaseListItem[], isLoadMore: boolean = false) => {
    const nextMenus: Record<string, ModelMenuInfo> = {};
    items.forEach((item) => {
      const result = buildModelMenuForItem(item);
      if (result) {
        nextMenus[item.id] = result;
      }
    });
    if (isLoadMore) {
      // 加载更多时，合并之前的菜单
      setModelMenus(prev => ({ ...prev, ...nextMenus }));
    } else {
      // 首次加载或刷新时，替换所有菜单
      setModelMenus(nextMenus);
    }
  };

  const fetchData = async (pageNum: number = 1, isLoadMore: boolean = false) => {
    if (!modelTypes.length) return;
    if (loading) return;
    
    console.log('fetchData called:', {
      pageNum,
      isLoadMore,
      currentQuery: query,
      currentFolderPath: folderPath,
      folderPathLastId: folderPath.length > 0 ? folderPath[folderPath.length - 1].id : 'none'
    });
    
    setLoading(true);
    try {
      const params = {
        ...query,
        page: pageNum,
        pagesize: 9,
        orderby:'created_at',
        desc:true,
      }
      
      console.log('API params:', params);
      const res = await getKnowledgeBaseList(undefined, params);
      const response = res as KnowledgeBaseListResponse & { items?: KnowledgeBaseListItem[] };
      console.log('API response:', response);
      const list = response.items || [];
      const curDatas = list.map((item: KnowledgeBaseListItem) => ({
        ...item,
        descriptionItems: formatData(item),
      }));
      
      if (isLoadMore) {
        setData(prev => [...prev, ...curDatas]);
      } else {
        setData(curDatas);
        // 重置分页状态，确保从第一页开始
        setPage(1);
      }

      // 更新是否有更多数据
      const hasNext = response.page?.has_next ?? false;
      console.log('hasNext:', hasNext, 'response.page:', response.page);
      setHasMore(hasNext);

      buildModelMenus(list, isLoadMore);
    } catch (error) {
      console.error('Failed to fetch knowledge base list:', error);
      if (!isLoadMore) {
        setData([]);
        setModelMenus({});
        setPage(1);
      }
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }

  const loadMore = () => {
    console.log('loadMore called, loading:', loading, 'hasMore:', hasMore, 'page:', page);
    if (loading || !hasMore) return;
    const nextPage = page + 1;
    setPage(nextPage);
    fetchData(nextPage, true);
  }
  
  // 创建一个稳定的刷新函数供子组件调用
  const handleRefresh = () => {
    fetchData(1, false);
  }

  
  const handleSearch = (value?: string) => {
    setQuery((prev) => ({
      ...prev,
      keywords: value,
    }))
  }
  // 处理编辑
  const handleEdit = (item: KnowledgeBaseListItem) => {
    modalRef?.current?.handleOpen(item, item.type);
  };

  // 处理删除
  const handleDelete = (item: KnowledgeBaseListItem) => {
    confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: item.name }),
      onOk: () => {
        deleteKnowledgeBase(item.id).then((res) => {
          if (res) {
            messageApi.success(t('common.deleteSuccess'));
            fetchData(1, false);
          }
        });
      },
      onCancel: () => {
        console.log('取消删除');
      },
    });
  };
  // 处理跳转详情
  const handleToDetail = useCallback((knowledgeBase: KnowledgeBaseListItem) => {
    // 统一处理类型判断，忽略大小写
    const itemType = (knowledgeBase.type || '').toLowerCase();
    
    console.log('handleToDetail called with:', {
      id: knowledgeBase.id,
      name: knowledgeBase.name,
      type: itemType,
      currentFolderPath: folderPath,
      currentQuery: query
    });
    
    // 如果是 Folder 类型，刷新当前页面，显示该文件夹下的知识库列表
    if (itemType === 'folder') {
      // 计算新的文件夹路径
      const newFolderPath = [
        ...folderPath,
        {
          id: knowledgeBase.id,
          name: knowledgeBase.name,
        },
      ];
      
      console.log('Folder clicked:', {
        folderId: knowledgeBase.id,
        folderName: knowledgeBase.name,
        currentFolderPath: folderPath,
        newFolderPath: newFolderPath
      });
      
      // 同步更新状态，保持与面包屑逻辑一致
      setFolderPath(newFolderPath);
      setQuery((prev) => ({
        ...prev,
        parent_id: knowledgeBase.id,
      }));
      
      return;
    }
    
    // 统一处理权限判断，忽略大小写
    const permissionId = (knowledgeBase.permission_id || '').toLowerCase();
    const isPrivate = permissionId === 'private';
    
    // 根据权限类型跳转到不同的详情页
    const targetPath = isPrivate 
      ? `/knowledge-base/${knowledgeBase.id}/private`
      : `/knowledge-base/${knowledgeBase.id}/share`;
    
    // 跳转时传递当前的文件夹路径信息
    const navigationState = {
      fromKnowledgeBaseList: true,
      knowledgeBaseFolderPath: folderPath,
      parentId: query.parent_id,
      timestamp: Date.now(), // 添加时间戳确保每次跳转状态都不同
    };
    
    // 检查是否是相同路径跳转
    const currentPath = location.pathname;
    
    if (currentPath === targetPath) {
      // 如果是相同路径，使用replace并强制刷新状态
      navigate(targetPath, { 
        state: navigationState, 
        replace: true 
      });
    } else {
      // 不同路径，正常跳转
      navigate(targetPath, { state: navigationState });
    }
  }, [folderPath, query, location.pathname, navigate])
  // 更新面包屑
  useEffect(() => {
    updateBreadcrumbs({
      knowledgeBaseFolderPath: folderPath,
      documentFolderPath: [],
    });
  }, [folderPath, updateBreadcrumbs]);

  // 处理从详情页返回的导航
  useEffect(() => {
    const state = location.state as {
      navigateToFolder?: string;
      folderPath?: Array<{ id: string; name: string }>;
      resetToRoot?: boolean;
    } | null;
    
    // 避免重复处理相同的状态
    if (state && state !== processedStateRef.current) {
      processedStateRef.current = state;
      
      if (state.resetToRoot) {
        // 重置到根目录
        setFolderPath([]);
        setQuery((prev) => ({
          ...prev,
          parent_id: undefined,
        }));
      } else if (state?.navigateToFolder && state?.folderPath) {
        // 恢复文件夹路径和查询状态
        setFolderPath(state.folderPath);
        setQuery((prev) => ({
          ...prev,
          parent_id: state.navigateToFolder,
        }));
      }
      
      // 不清除 state，避免干扰后续导航
      // 使用 processedStateRef 来避免重复处理相同的 state
    }
  }, [location.state, navigate]);

  useEffect(() => {
    fetchModelTypes();
    fetchKnowledgeBaseTypes();
    fetchModelList();
  }, [])
  useEffect(() => {
    if (modelTypes.length) {
      fetchData(1, false);
    }
  }, [modelTypes, query.parent_id, query.keywords, query.orderby, query.desc])

  return (
    <>
      {contextHolder}
      <div className="rb:flex rb:justify-between rb:px-2 rb:mb-4">
        <SearchInput
          placeholder={t('knowledgeBase.searchPlaceholder')}
          onSearch={handleSearch}
          style={{ width: '32.666%' }}
        />
        
        <Dropdown menu={{ items: createItems }} trigger={['click']}>
          <Button type="primary">+ {t('knowledgeBase.createKnowledgeBase')}</Button>
        </Dropdown>
      </div>
      <div id="scrollableDiv" style={{ height: 'calc(100vh - 120px)', overflowY: 'auto', overflowX: 'hidden' }}>
      <InfiniteScroll
        dataLength={data.length}
        next={loadMore}
        hasMore={hasMore && !loading}
        loader={<div className="rb:text-center rb:py-4">{t('common.loading')}</div>}
        endMessage={
          data.length > 0 ? (
            <div className="rb:text-center rb:py-4 rb:text-gray-400">
              {t('common.noMoreData')}
            </div>
          ) : null
        }
        
        scrollThreshold={0.9}
        scrollableTarget="scrollableDiv"
        style={{ overflow: 'visible', width: '100%' }}
      >
        {data.length === 0 && !loading ? (
          <Empty size={200} />
        ) : (
          <Row gutter={[16, 16]} className="rb:mb-2" style={{ margin: 0 }}>
            {data.map((item) => {
            const modelInfo = modelMenus[item.id];
            const hasModelInfo = modelInfo && modelInfo.menu.length > 1;
            return (
              <Col xs={12} sm={12} md={12} lg={8} xl={8} key={item.id} >
                <RbCard
                  title={item.name}
                  className='rb:min-h-[198px]'
                  extra={
                    <div onClick={(e) => e.stopPropagation()}>
                      <Dropdown menu={{ items: getOptMenuItems(item) }} >
                        <EllipsisOutlined className="rb:cursor-pointer" />
                      </Dropdown>
                    </div>
                  }
                >
                  <div className='rb:min-h-[158px]' onClick={() => handleToDetail(item)}>
                    <div className='rb:min-h-[124px]'>
                    {item.descriptionItems?.map((description: Record<string, unknown>) => (
                        <div 
                        key={description.key as string}
                        className="rb:flex rb:gap-4 rb:justify-start rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px]"
                        >
                        <div className="rb:whitespace-nowrap rb:w-20">{(description.label as string)}</div>
                        <div className={clsx('rb:flex-inline rb:text-left rb:py-[1px] rb:rounded rb:font-medium',{
                            "rb:text-[#155eef] rb:bg-[rgba(21,94,239,0.06)] rb:px-2 rb:border rb:border-[rgba(21,94,239,0.25)] rb:font-medium": (description.key as string) === 'permission_id' && (description.children as string) === t('knowledgeBase.private'),
                            "rb:text-[#369F21] rb:bg-[rgba(54,159,33,0.06)] rb:px-2 rb:border rb:border-[rgba(54,159,33,0.25);] rb:font-medium": (description.key as string) === 'permission_id' && (description.children as string) === t('knowledgeBase.share'),
                        })}>{(description.children as string)}</div>
                        </div>
                    ))}
                    {item.description && (
                        <div className="rb:flex rb:text-[#5B6167] rb:h-10 rb:line-clamp-2 rb:text-sm rb:leading-5 rb:mb-3 rb:gap-4">
                        <div className="rb:font-medium rb:w-20">{t('knowledgeBase.description')} </div>
                        <Tooltip title={item.description}>
                          <div className='rb:flex-1 rb:text-left rb:leading-5 rb:text-gray-800 rb:break-words rb:line-clamp-2'>{item.description || t('knowledgeBase.noDescription')}</div>
                        </Tooltip>
                        </div>
                    )}
                    </div>
                    {hasModelInfo && (
                      <Dropdown menu={{ items: modelInfo.menu }}>
                        <div
                          className="rb:flex rb:text-gray-500 rb:px-3 rb:py-2 rb:text-[12px] rb:leading-4 rb:mb-2 rb:bg-[#F0F3F8] rb:rounded"
                          onClick={(e) => e.stopPropagation()}
                        >
                            <span>{t('knowledgeBase.models')}:</span>
                            <span className="rb:ml-1 rb:truncate rb:max-w-[200px]">
                              {modelInfo.summary.join('、')}
                            </span>
                        </div>
                      </Dropdown>
                    )}
                  </div>
                </RbCard>
              </Col>
            )})}
          </Row>
        )}
      </InfiniteScroll>

      <CreateModal
        ref={modalRef}
        refreshTable={handleRefresh}
      />
      </div>
    </>
  )
}

export default KnowledgeBaseManagement

