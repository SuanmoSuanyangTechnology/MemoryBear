import React, { useState, useRef, useEffect, useCallback, type ReactNode } from 'react';
import { Button, App, Space, Row, Col, Flex, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import InfiniteScroll from 'react-infinite-scroll-component';
import clsx from 'clsx'

import MarketConfigModal, { type MarketConfigModalRef } from './components/MarketConfigModal';
import McpServiceModal from './components/McpServiceModal';
import type { McpServiceModalRef } from './types';
import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png'
import Empty from '@/components/Empty/index'
import { getMarketTools, getMarketConfig, getMarketMCPs, getMarketMCPDetail, getMarketMCPsActivated, getTools } from '@/api/tools';
import SearchInput from '@/components/SearchInput';
import RbCard from '@/components/RbCard'
import Tag from '@/components/Tag'
import marketIcon from '@/assets/images/tool/market.png'

interface MarketSource {
  id: string;
  name: string;
  category: string;
  logo_url: string;
  url: string;
  description: string;
  api_key?: string;
  connected: boolean;
  mcp_count: number;
  created_at?: number;
  created_by?: string;
}

interface MarketMcp {
  id: string;
  name: string;
  chinese_name?: string;
  description: string;
  logo_url: string;
  publisher: string;
  categories?: string[];
  tags?: string[];
  view_count?: number;
  activated?: boolean;
  inDatabase?: boolean;
  locales?: {
    [lang: string]: {
      name: string;
      description: string;
    };
  };
}

interface MarketCategory {
  id: string;
  name: string;
}

interface MarketApiResponse {
  items: MarketSource[];
}

const Market: React.FC<{ getStatusTag?: (status: string) => ReactNode }> = () => {
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();

  const getLocaleField = (mcp: MarketMcp, field: 'name' | 'description') => {
    const lang = i18n.language?.startsWith('zh') ? 'zh' : 'en';
    return mcp.locales?.[lang]?.[field] || mcp[field] || '';
  };
  const [loading, setLoading] = useState(false);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const marketConfigModalRef = useRef<MarketConfigModalRef>(null);
  const mcpServiceModalRef = useRef<McpServiceModalRef>(null);
  const [marketSources, setMarketSources] = useState<MarketSource[]>([]);
  const [categories, setCategories] = useState<MarketCategory[]>([]);
  const [mcpCache, setMcpCache] = useState<Record<string, MarketMcp[]>>({});
  const [mcpTotal, setMcpTotal] = useState(0);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [configIdMap, setConfigIdMap] = useState<Record<string, string>>({});
  const [hasMore, setHasMore] = useState(false);
  const [activatedMcps, setActivatedMcps] = useState<string[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 20;
  const searchTimerRef = useRef<number | null>(null);

  // 获取市场数据
  useEffect(() => {
    const fetchMarketData = async () => {
      setLoading(true);
      try {
        const response = await getMarketTools({}) as MarketApiResponse;
        if (response?.items && Array.isArray(response.items)) {
          setMarketSources(response.items);
          
          // 根据 category 字段分组
          const categoryMap = new Map<string, MarketCategory>();
          response.items.forEach(item => {
            if (item.category && !categoryMap.has(item.category)) {
              categoryMap.set(item.category, {
                id: item.category,
                name: item.category
              });
            }
          });
          
          setCategories(Array.from(categoryMap.values()));
          if (response.items[0]?.id) {
            handleSelectSource(response.items[0]?.id)
          }
        }
      } catch (error) {
        console.error('获取市场数据失败:', error);
        message.error('获取市场数据失败');
      } finally {
        setLoading(false);
      }
    };

    fetchMarketData();
  }, [message]);

  const fetchMcpList = async (sourceId: string, page = 1, append = false, keywords = '') => {
    setLoading(true);
    try {
      let configId = configIdMap[sourceId];

      // 如果没有缓存 configId，先获取配置
      if (!configId) {
        const config: any = await getMarketConfig(sourceId);
        if (config?.id) {
          configId = config.id;
          setConfigIdMap(prev => ({ ...prev, [sourceId]: configId }));
        } else {
          return;
        }
      }

      // 第一次加载时获取已激活列表
      let activatedIds: string[] = activatedMcps;
      if (page === 1 && !append) {
        const activatedRes: any = await getMarketMCPsActivated({ mcp_market_config_id: configId });
        if (activatedRes && Array.isArray(activatedRes)) {
          activatedIds = activatedRes.map((item: any) => item.id);
          setActivatedMcps(activatedIds);
        }
      }

      // 获取全量工具列表，用于标记已入库的 MCP
      const allTools: any = await getTools({ tool_type: 'mcp' });
      const toolsList = Array.isArray(allTools) ? allTools : [];

      const res: any = await getMarketMCPs({ 
        mcp_market_config_id: configId, 
        page, 
        pagesize: pageSize,
        ...(keywords ? { keywords } : {})
      });
      if (res?.items && Array.isArray(res.items)) {
        // 标记已激活和已入库的 MCP
        const mcpsWithActivated = res.items.map((item: MarketMcp) => {
          // 检查是否已入库：market_id = sourceId, market_config_id = configId, mcp_service_id = item.id
          const isInDatabase = toolsList.some((tool: any) => 
            tool.config_data?.market_id === sourceId &&
            tool.config_data?.market_config_id === configId &&
            tool.config_data?.mcp_service_id === item.id
          );
          
          return {
            ...item,
            activated: activatedIds.includes(item.id),
            inDatabase: isInDatabase
          };
        });
        
        setMcpCache(prev => ({
          ...prev,
          [sourceId]: append ? [...(prev[sourceId] || []), ...mcpsWithActivated] : mcpsWithActivated
        }));
      }
      if (res?.page) {
        setMcpTotal(res.page.total || 0);
        setHasMore(!!res.page.has_next);
        setCurrentPage(res.page.page || page);
      }
    } catch (error) {
      console.error('获取 MCP 列表失败:', error);
    } finally {
      setLoading(false);
    }
  };

  const loadMore = useCallback(() => {
    if (!selectedSource || loading) return;
    fetchMcpList(selectedSource, currentPage + 1, true, searchKeyword);
  }, [selectedSource, currentPage, loading, searchKeyword]);

  const handleSearchChange = (value: string) => {
    setSearchKeyword(value);
    
    // 清除之前的定时器
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current);
    }
    
    // 如果清空搜索框，恢复原始列表
    if (!value.trim()) {
      if (selectedSource) {
        // 清除缓存，重新加载原始列表
        setMcpCache(prev => {
          const next = { ...prev };
          delete next[selectedSource];
          return next;
        });
        setCurrentPage(1);
        fetchMcpList(selectedSource, 1, false, '');
      }
      return;
    }
    
    // 设置新的定时器，500ms 后执行搜索
    searchTimerRef.current = setTimeout(() => {
      if (selectedSource) {
        // 清除缓存，重新搜索
        setMcpCache(prev => {
          const next = { ...prev };
          delete next[selectedSource];
          return next;
        });
        setCurrentPage(1);
        fetchMcpList(selectedSource, 1, false, value);
      }
    }, 500);
  };

  const handleSelectSource = async (sourceId: string) => {
    if (sourceId === selectedSource) return
    setSelectedSource(sourceId);
    setSearchKeyword('');
    setCurrentPage(1);
    setHasMore(false);
    setMcpTotal(0);

    // 如果缓存中已有数据，直接使用
    if (mcpCache[sourceId]) return;

    await fetchMcpList(sourceId, 1);
  };

  const handleOpenConfig = async (sourceId: string) => {
    const source = marketSources.find(s => s.id === sourceId);
    if (!source) return;
    try {
      const config: any = await getMarketConfig(sourceId);
      console.log('获取到的配置数据:', config);
      marketConfigModalRef.current?.handleOpen({
        ...source,
        connected: config?.status === 1,
        token: config?.token || '',
        configId: config?.id || '',
      });
    } catch {
      marketConfigModalRef.current?.handleOpen(source);
    }
  };

  const handleOpenMcpServiceModal = async (mcp: MarketMcp) => {
    if (!selectedSource || !configIdMap[selectedSource]) return;
    try {
      const detail: any = await getMarketMCPDetail({
        mcp_market_config_id: configIdMap[selectedSource],
        server_id: mcp.id,
      });
      const source = marketSources.find(s => s.id === selectedSource);
      const toolItem = {
        name: detail.name,
        description: detail.description,
        source_channel: source?.name || '',
        market_id: selectedSource,
        market_config_id: configIdMap[selectedSource],
        mcp_service_id: mcp.id,
        config_data: {
          server_url: detail.servers?.[0]?.url || '',
          connection_config: {
            auth_type: 'none',
            timeout: 30,
            headers: {},
          },
        },
      };
      mcpServiceModalRef.current?.handleOpen(toolItem as any);
    } catch (error) {
      console.error('获取 MCP 服务详情失败:', error);
    }
  };

  const handleConnect = async (sourceId: string, configId: string) => {
    // 更新市场源状态，缓存 configId
    setMarketSources(prev => prev.map(source => {
      if (source.id === sourceId) {
        return { ...source, connected: true };
      }
      return source;
    }));
    setConfigIdMap(prev => ({ ...prev, [sourceId]: configId }));

    // 使用 fetchMcpList 获取完整的 MCP 列表（包含激活状态和入库状态）
    await fetchMcpList(sourceId, 1);
  };

  const handleRefreshAfterAdd = async () => {
    // 添加成功后，刷新当前选中的市场源的 MCP 列表
    if (!selectedSource) return;
    
    // 清除缓存并重新加载，这样会重新获取工具列表并更新 inDatabase 标记
    setMcpCache(prev => {
      const next = { ...prev };
      delete next[selectedSource];
      return next;
    });
    setCurrentPage(1);
    await fetchMcpList(selectedSource, 1);
  };

  const renderSourceDetail = () => {
    if (!selectedSource) {
      return (
        <div className="rb:flex rb:flex-col rb:items-center rb:justify-center rb:h-full rb:text-center">
          <Empty
            url={pageEmptyIcon}
            title={t('tool.marketSelectTitle')}
            subTitle={t('tool.marketSelectDesc')}
            size={200}
            className="rb:h-full"
          />

        </div>
      );
    }

    const source = marketSources.find(s => s.id === selectedSource);
    if (!source) return null;

    const mcpList = mcpCache[selectedSource] || [];

    return (
      <>
        <Flex justify="space-between" align="center">
          <Flex gap={12} align="center" className="rb:pl-1!">
            <Flex align="center" justify="center" className="rb:size-12">
              {source.logo_url ? (
                <img
                  src={source.logo_url}
                  alt={source.name}
                  className="rb:w-full rb:h-full rb:object-cover  rb:rounded-xl"
                  referrerPolicy="no-referrer"
                  onError={(e) => {
                    e.currentTarget.src = marketIcon
                  }}
                />
              ) : (
                <div className="rb:size-12  rb:rounded-xl rb:bg-cover rb:bg-[url('@/assets/images/tool/market.png')]"></div>
              )}
            </Flex>
            <div>
              <div className="rb:font-[MiSans Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{source.name}</div>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t('tool.availableMcp')} ({mcpTotal})</div>
            </div>
          </Flex>

          <Space size={12}>
            <SearchInput
              placeholder={t('tool.marketSearchPlaceholder')}
              value={searchKeyword}
              onSearch={(value: string) => handleSearchChange(value)}
              allowClear
              style={{ width: 200 }}
            />
            <Button type="primary" ghost onClick={() => handleOpenConfig(selectedSource)}>
              {t('tool.marketConfigBtn')}
            </Button>
            <Button type="primary" onClick={() => window.open(source.url, '_blank')}>
              {t('tool.marketVisit')}
            </Button>
          </Space>
        </Flex>

        <div className="rb:mt-4">
          <div id="mcpScrollableDiv" className="rb:overflow-y-auto rb:h-[calc(100vh-188px)]">
            {!loading && mcpList.length === 0 ? (
              <Empty
                url={pageEmptyIcon}
                title={searchKeyword ? t('tool.marketNoSearchResult') : t('tool.marketNoData')}
                subTitle={searchKeyword ? t('tool.marketNoSearchResultDesc') : t('tool.marketNoDataDesc')}
                size={200}
                className="rb:h-full"
              />
            ) : (
            <InfiniteScroll
              dataLength={mcpList.length}
              next={loadMore}
              hasMore={hasMore}
              loader={null}
              scrollableTarget="mcpScrollableDiv"
            >
              <Row gutter={[12,12]}>
                {mcpList.map(mcp => (
                  <Col
                    key={mcp.id}
                    span={12}
                  >
                    <RbCard
                      avatarUrl={mcp.logo_url || marketIcon}
                      title={
                        <Flex justify="space-between" gap={16}>
                          <Flex vertical gap={6}>
                            <Tooltip title={getLocaleField(mcp, 'name')}>
                              <div className="rb:wrap-break-word rb:line-clamp-1">{getLocaleField(mcp, 'name')}</div>
                            </Tooltip>
                            <Flex gap={8} wrap className='rb:wrap-break-word rb:line-clamp-1'>
                              {mcp.categories?.[0] && (
                                <Tag>{mcp.categories[0]}</Tag>
                              )}
                              {mcp.activated && <Tag color="success">{t('tool.marketActivated')}</Tag>}
                              {mcp.inDatabase && <Tag>{t('tool.marketInDatabase')}</Tag>}
                            </Flex>
                          </Flex>
                          <Button
                            disabled={mcp.inDatabase}
                            size="small"
                            onClick={() => handleOpenMcpServiceModal(mcp)}
                          >+</Button>
                        </Flex>
                      }
                      isNeedTooltip={false}
                      footer={<Flex justify="space-between" align="center" className="rb:text-[#5B6167] rb:text-[12px] rb:mb-1!">
                        {mcp.publisher && <span>{mcp.publisher.startsWith('@') ? mcp.publisher : `@${mcp.publisher}`}</span>}
                        {mcp.view_count && <Space size={4}>
                          <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/global_outline.svg')]"></div>
                          {mcp.view_count.toLocaleString()}
                        </Space>}
                      </Flex>}
                    >
                      {getLocaleField(mcp, 'description') ?
                        <Tooltip title={getLocaleField(mcp, 'description')}>
                          <div className="rb:h-10 rb:leading-5 rb:wrap-break-word rb:line-clamp-2 rb:mt-2">{getLocaleField(mcp, 'description')}</div>
                        </Tooltip>
                        : <div className="rb:h-10 rb:leading-5 rb:text-[#A8A9AA] rb:mt-2">{t('tool.descEmpty')}</div>  
                      }
                    </RbCard>
                  </Col>
                ))}
              </Row>
            </InfiniteScroll>
            )}
          </div>
        </div>
      </>
    );
  };

  return (
    <Row gutter={16}>
      <Col flex="380px">
        <Flex vertical gap={16}>
          <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:leading-5.5">{t('tool.mcpMarket')}</div>
          {categories.map(cat => (
            <Flex key={cat.id} vertical gap={8}>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:font-medium rb:leading-4.5">
                {cat.name}
              </div>
              {marketSources
                .filter(s => s.category === cat.id)
                .map(source => (
                  <Flex
                    key={source.id}
                    align="center"
                    gap={8}
                    className={clsx('rb:bg-white rb:rounded-xl rb:py-2! rb:px-3! rb:cursor-pointer rb:transition-all', {
                      'rb:border rb:border-[#171719]': selectedSource === source.id,
                      'rb:shadow-[0px_2px_6px_0px_rgba(23,23,25,0.1)]': selectedSource !== source.id
                    })}
                    onClick={() => handleSelectSource(source.id)}
                  >
                    <div className="rb:size-7 rb:shrink-0 rb:flex rb:items-center rb:justify-center rb:overflow-hidden rb:rounded rb:bg-gray-100">
                      {source.logo_url ? (
                        <img
                          src={source.logo_url}
                          alt={source.name}
                          className="rb:w-full rb:h-full rb:object-cover rb:rounded-sm"
                          referrerPolicy="no-referrer"
                          onError={(e) => {
                            e.currentTarget.src = marketIcon;
                          }}
                        />
                      ) : (
                        <div className="rb:size-7 rb:rounded-sm rb:bg-cover rb:bg-[url('@/assets/images/tool/market.png')]"></div>
                      )}
                    </div>
                    <span className="rb:flex-1 rb:font-medium rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap">
                      {source.name}
                    </span>
                  </Flex>
                ))}
            </Flex>
          ))}
        </Flex>
      </Col>
      <Col flex="1">
        {renderSourceDetail()}
      </Col>
      {/* 配置弹窗 */}
      <MarketConfigModal
        ref={marketConfigModalRef}
        onConnect={handleConnect}
      />
      <McpServiceModal
        ref={mcpServiceModalRef}
        refresh={handleRefreshAfterAdd}
      />
    </Row>
  );
};

export default Market;
