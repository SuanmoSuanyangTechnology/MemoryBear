import { useEffect, useState, useRef, useMemo, useCallback, type FC } from 'react';
import { Button, Dropdown, Tooltip, App, Flex } from 'antd'
import type { MenuProps } from 'antd';
import { RightOutlined, DownOutlined } from '@ant-design/icons';
import { useNavigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { KnowledgeBaseListItem, CreateModalRef, ListQuery } from '@/views/KnowledgeBase/types'
import CreateModal from './components/CreateModal'
import RbCard from '@/components/RbCard/Card'
import SearchInput from '@/components/SearchInput'
import { getKnowledgeBaseListUrl, deleteKnowledgeBase, getKnowledgeBaseTypeList } from '@/api/knowledgeBase'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { getModelList } from '@/api/models'
import type { Model } from '@/views/ModelManagement/types'
import copy from 'copy-to-clipboard'
import CopyModal, { type CopyModalRef } from './components/CopyModal';
import { baseModelFields, multimodalModelFields, getTypeIcon, menuItems } from './constants';
import ModelStatusTag from '@/components/ModelSelect/ModelStatusTag'
import type { ModelDisplayItem, ModelMenuInfo, KnowledgeBaseNavigationState } from './types'
import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import { getFolderPathAfterEntering } from './folderPath';

const KnowledgeBaseManagement: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { modal, message: messageApi } = App.useApp()
  const location = useLocation();
  const initialNavigationState = location.state as KnowledgeBaseNavigationState | null;
  const initialFolderId = initialNavigationState?.resetToRoot
    ? undefined
    : initialNavigationState?.navigateToFolder;
  const initialFolderPath = initialFolderId && initialNavigationState?.folderPath
    ? initialNavigationState.folderPath
    : [];
  const pageScrollListRef = useRef<PageScrollListRef>(null)
  const [query, setQuery] = useState<ListQuery>({
    orderby:'created_at',
    desc:true,
    parent_id: initialFolderId,
  })
  const [modelListVersion, setModelListVersion] = useState(0);
  const [expandedItems, setExpandedItems] = useState<Record<string, boolean>>({});
  const [knowledgeBaseTypes, setKnowledgeBaseTypes] = useState<string[]>([]);
  const modelListCache = useRef<Partial<Record<string, Model>>>({});
  const modalRef = useRef<CreateModalRef>(null)
  const processedStateRef = useRef<any>(null);
  const copyModalRef = useRef<CopyModalRef>(null)
  const loadingRef = useRef(false);
  const latestRequestIdRef = useRef(0);
  const autoLoadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  
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
  const [folderPath, setFolderPath] = useState<BreadcrumbItem[]>(initialFolderPath);
  
  // 生成下拉菜单项（根据当前 item）
  const getOptMenuItems = (item: KnowledgeBaseListItem): MenuProps['items'] => {
    const permissionId = (item.permission_id || '').toLowerCase();
    const isShared = permissionId === 'share';
    const isFolder = item.type === 'Folder';
    return menuItems.filter(menu =>
      menu.key === 'delete' || (menu.key === 'edit' && isShared)
      || (menu.key === 'copy' && (isShared || isFolder))
    ).map(menu => {
      return {
        ...menu,
        label: t(`knowledgeBase.${menu.key}`),
        icon: <div className={clsx(`rb:size-4 rb:bg-cover`, menu.icon)} />,
        onClick: () => {
          switch (menu.key) {
            case 'edit':
              handleEdit(item);
              break
            case 'copy':
              handleCopyKb(item);
              break
            case 'delete':
              handleDelete(item);  
              break
            default:
              break
          }
        }
      }
    })
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
    
    modalRef?.current?.handleOpen(record, type)
  }, [folderPath, query.parent_id])

  // 动态生成 createItems
  const createItems: MenuProps['items'] = useMemo(() => {
    return knowledgeBaseTypes.map((type, index) => {
      const typeLabel = type.toLocaleLowerCase();
      return {
        key: String(index + 1),
        icon: <img src={getTypeIcon(type)} alt={type} className="rb:size-4" />,
        label: t(`knowledgeBase.${typeLabel === 'third-party' ? 'tp': typeLabel}`),
        onClick: () => {
          handleCreate(type);
        },
      }
    });
  }, [knowledgeBaseTypes, t, handleCreate]);
  const formatData = (data: KnowledgeBaseListItem) => {
    const keys: (keyof KnowledgeBaseListItem)[] = ['permission_id','type']
    return keys.map(key => ({
      key,
      label: t(`knowledgeBase.${key}`),
      children: key === 'permission_id' 
        ? ((data[key] || '').toLowerCase() === 'private' ? t('knowledgeBase.private') : t('knowledgeBase.share'))
        : String(data[key] || '-'),
    }))
  }
  const fetchModelList = () => { 
    getModelList()
      .then(res => {
        const response = res as Model[] || []
        if (Array.isArray(response)) {
          const cache: Record<string, Model> = {};
          response.forEach((model: any) => {
            if (model.id && model.name) {
              cache[model.id] = model;
            }
          });
          modelListCache.current = cache;
          setModelListVersion(version => version + 1);
        }
      })
  };
  const fetchKnowledgeBaseTypes = () => {
    getKnowledgeBaseTypeList()
      .then((res) => {
        setKnowledgeBaseTypes(res);
      })
      .catch(() => {
        setKnowledgeBaseTypes([]);
      })
  };
  const buildModelMenuForItem = (item: KnowledgeBaseListItem): ModelMenuInfo | null => {
    const record = item as unknown as Record<string, unknown>;
    const menu: ModelDisplayItem[] = [];

    const addModelFields = (modelFields: readonly (typeof baseModelFields[number] | typeof multimodalModelFields[number])[]) => {
      for (const modelField of modelFields) {
        const fieldKey = `${modelField.name}_id`;
        const modelId = record[fieldKey] as string | undefined;
        const model = modelId ? modelListCache.current[modelId] : undefined;
        const modelName = model?.name;
        if (!modelName || !modelId || !model) continue;

        const typeLabel = t(`knowledgeBase.createForm.${fieldKey}`) || t(`knowledgeBase.${fieldKey}`) || modelField.type;
        menu.push({
          key: `${fieldKey}_${modelId}`,
          label: (
            <div className="rb:text-gray-500 rb:text-[12px]">
              {typeLabel}: <span className="rb:text-gray-900">{modelName}</span> <ModelStatusTag model={model} size="small" />
            </div>
          ),
        });
      }
    };
    addModelFields(baseModelFields);
    addModelFields(multimodalModelFields);

    if (!menu.length) return null;
    menu.unshift({
      key: 'header',
      label: <span className="rb:font-medium">{t('knowledgeBase.allModels')}</span>,
      disabled: true,
    });
    return { menu };
  };
  const handleRefresh = () => {
    pageScrollListRef.current?.refresh();
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
  const handleCopyKb = (item: KnowledgeBaseListItem) => {
    copyModalRef.current?.handleOpen(item)
  }
  // 处理删除
  const handleDelete = (item: KnowledgeBaseListItem) => {
    modal.confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: item.name }),
      onOk: () => {
        deleteKnowledgeBase(item.id).then((res) => {
          if (res) {
            messageApi.success(t('common.deleteSuccess'));
            handleRefresh();
          }
        });
      },
    });
  };
  // 处理跳转详情
  const handleToDetail = useCallback((knowledgeBase: KnowledgeBaseListItem) => {
    // 统一处理类型判断，忽略大小写
    const itemType = (knowledgeBase.type || '').toLowerCase();
    
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

    // 如果是相同路径，使用replace并强制刷新状态
    navigate(targetPath, { 
      state: navigationState, 
      replace: currentPath === targetPath 
    });
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
    const state = location.state as KnowledgeBaseNavigationState | null;
    
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
    fetchKnowledgeBaseTypes();
    fetchModelList();
  }, [])
  const handleCopy = (value: string) => {
    copy(value)
    messageApi.success(t('common.copySuccess'))
  }

  return (
    <Flex vertical gap={16} className="rb:h-full! rb:min-h-0! rb:overflow-hidden!">
      <Flex justify="space-between">
        <SearchInput
          placeholder={t('knowledgeBase.searchPlaceholder')}
          onSearch={handleSearch}
          className="rb:w-[32.666%]!"
        />
        
        <Dropdown menu={{ items: createItems }} trigger={['click']}>
          <Button type="primary">+ {t('knowledgeBase.createKnowledgeBase')}</Button>
        </Dropdown>
      </Flex>
      <PageScrollList<KnowledgeBaseListItem, ListQuery>
        ref={pageScrollListRef}
        url={getKnowledgeBaseListUrl}
        query={query}
        column={1}
        className="rb:flex-1! rb:min-h-0!"
        renderItems={(items) => {
          // 模型列表异步加载后通过 modelListVersion 触发重新派生。
          void modelListVersion;
          const columns: KnowledgeBaseListItem[][] = [[], [], []];
          const modelMenus: Record<string, ModelMenuInfo> = {};
          items.forEach((item, index) => {
            columns[index % columns.length].push({
              ...item,
              descriptionItems: formatData(item),
            });
            const modelInfo = buildModelMenuForItem(item);
            if (modelInfo) modelMenus[item.id] = modelInfo;
          });

          return (
            <Flex key="knowledge-base-items" align="flex-start" gap={12} className="rb:mb-2!">
              {columns.map((columnItems, colIdx) => (
                <div key={colIdx} style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {columnItems.map((item) => {
                    const modelInfo = modelMenus[item.id];
                    const hasModelInfo = modelInfo && modelInfo.menu.length > 1;
                    return (
                      <RbCard
                        key={item.id}
                        title={item.name}
                        headerType="borderless"
                        bodyClassName="rb:px-4! rb:pt-0! rb:pb-4!"
                        headerClassName="rb:py-3!"
                        className="rb:cursor-pointer"
                        onClick={() => handleToDetail(item)}
                        extra={
                          <div onClick={(e) => e.stopPropagation()}>
                            <Dropdown
                              menu={{ items: getOptMenuItems(item) }}
                              placement="bottomRight"
                            >
                              <div onClick={(e) => e.stopPropagation()} className="rb:cursor-pointer rb:size-5.5 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"></div>
                            </Dropdown>
                          </div>
                        }
                      >
                        <Tooltip title={item.description}>
                          <div className='rb:flex-1 rb:text-left rb:text-sm rb:leading-5 rb:text-gray-600 rb:wrap-break-word rb:line-clamp-1 rb:mb-3'>
                            {(item.description && item.description != '') ? item.description : t('knowledgeBase.noDescription')}
                          </div>
                        </Tooltip>
                        <Flex vertical gap={4} className='rb:min-h-15 rb:py-2.5! rb:px-3! rb:bg-gray-100 rb:rounded-lg rb:mb-3!'>
                          <div className="rb:cursor-pointer rb:mb-3 rb:w-full" onClick={() => handleCopy(item.id)}>
                            <div className="rb:text-gray-800 rb:font-medium">ID:</div>
                            <Flex align="center" className="rb:text-gray-600">
                              {item.id}
                              <span className="rb:ml-1 rb:inline-block rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></span>
                            </Flex>
                          </div>
                          {item.descriptionItems?.map((description: Record<string, unknown>) => (
                            <div 
                              key={description.key as string}
                              className="rb:grid rb:grid-cols-2 rb:text-gray-600 rb:text-[14px] rb:leading-5"
                            >
                              <div className={clsx('rb:whitespace-nowrap rb:w-20', {"rb:text-gray-800 rb:font-medium" : (description.key as string) === 'permission_id'})}>{(description.label as string)}</div>
                              <div className={clsx('rb:flex-inline rb:text-left rb:py-px rb:rounded',{
                                  "rb:text-blue-500 rb:font-medium": (description.key as string) === 'permission_id' && (description.children as string) === t('knowledgeBase.private'),
                                  "rb:text-[#FF8A4C] rb:font-medium": (description.key as string) === 'permission_id' && (description.children as string) === t('knowledgeBase.share'),
                              })}>{(description.children as string)}</div>
                            </div>
                          ))}
                        </Flex>
                        {hasModelInfo && (
                          <div onClick={(e) => e.stopPropagation()}>
                            <Flex
                              align="center"
                              justify="space-between"
                              className="rb:pt-2! rb:pr-1! rb:text-[12px] rb:leading-5 rb:cursor-pointer rb:rounded rb:transition-colors"
                              onClick={() => {
                                setExpandedItems(prev => ({ ...prev, [item.id]: !prev[item.id] }));
                              }}
                            >
                              <span className="rb:truncate rb:flex-1 rb:text-gray-500">
                                {modelInfo.menu.slice(1, 2).map(item => item?.label ? <div>{item?.label}</div> : null)}
                              </span>
                              <span className="rb:text-gray-400 rb:text-[10px]">
                                {expandedItems[item.id] ? <DownOutlined /> : <RightOutlined />}
                              </span>
                            </Flex>
                            {expandedItems[item.id] && (
                              <Flex vertical gap={8} className="rb:text-[12px] rb:mt-2!">
                                {modelInfo.menu.slice(2).map((text, idx) => {
                                  return (
                                    <div key={idx} className="rb:text-gray-500">
                                      {text?.label}
                                    </div>
                                  );
                                })}
                              </Flex>
                            )}
                          </div>
                        )}
                      </RbCard>
                    )
                  })}
                </div>
              ))}
            </Flex>
          );
        }}
      />

      <CreateModal
        ref={modalRef}
        refreshTable={handleRefresh}
      />
      <CopyModal ref={copyModalRef} refresh={handleRefresh} />
    </Flex>
  )
}

export default KnowledgeBaseManagement
