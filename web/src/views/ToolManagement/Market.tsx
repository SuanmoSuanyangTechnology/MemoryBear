import React, { useState, useRef, useEffect, useCallback, type ReactNode } from 'react';
import { Input, Button, App, Card, Space, Skeleton, Tag } from 'antd';
import { SearchOutlined, SettingOutlined, GlobalOutlined, SyncOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import InfiniteScroll from 'react-infinite-scroll-component';
import MarketConfigModal, { type MarketConfigModalRef } from './components/MarketConfigModal';
import McpServiceModal from './components/McpServiceModal';
import type { McpServiceModalRef } from './types';
import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png'
import Empty from '@/components/Empty/index'
import { getMarketTools, getMarketConfig, getMarketMCPs, getMarketMCPDetail, getMarketMCPsActivated, getTools } from '@/api/tools';
import BodyWrapper from '@/components/Empty/BodyWrapper';
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

  const fetchMcpList = async (sourceId: string, page = 1, append = false) => {
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

      const res: any = await getMarketMCPs({ mcp_market_config_id: configId, page, pagesize: pageSize });
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
    fetchMcpList(selectedSource, currentPage + 1, true);
  }, [selectedSource, currentPage, loading]);

  const handleSelectSource = async (sourceId: string) => {
    setSelectedSource(sourceId);
    setSearchKeyword('');
    setCurrentPage(1);
    setHasMore(false);
    setMcpTotal(0);

    // 如果缓存中已有数据，直接使用
    if (mcpCache[sourceId]) return;

    await fetchMcpList(sourceId, 1);
  };

  const handleRefresh = async (sourceId: string) => {
    // 清除缓存，重新从第一页加载
    setMcpCache(prev => {
      const next = { ...prev };
      delete next[sourceId];
      return next;
    });
    setCurrentPage(1);
    await fetchMcpList(sourceId, 1);
    const source = marketSources.find(s => s.id === sourceId);
    if (source) {
      message.success(`${source.name} ${t('tool.marketRefreshSuccess')}`);
    }
  };

  const handleOpenConfig = async (sourceId: string) => {
    const source = marketSources.find(s => s.id === sourceId);
    if (!source) return;
    try {
      const config: any = await getMarketConfig(sourceId);
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

    // 用 configId 获取第一页 MCP 列表
    try {
      const res: any = await getMarketMCPs({ mcp_market_config_id: configId, page: 1, pagesize: pageSize });
      if (res?.items && Array.isArray(res.items)) {
        setMcpCache(prev => ({ ...prev, [sourceId]: res.items }));
      }
      if (res?.page) {
        setMcpTotal(res.page.total || 0);
        setHasMore(!!res.page.has_next);
        setCurrentPage(1);
      }
    } catch (error) {
      console.error('获取 MCP 列表失败:', error);
    }
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
    const filteredList = mcpList.filter(mcp => {
      const name = getLocaleField(mcp, 'name');
      const desc = getLocaleField(mcp, 'description');
      return name.toLowerCase().includes(searchKeyword.toLowerCase()) ||
        desc.toLowerCase().includes(searchKeyword.toLowerCase());
    });

    return (
      <>
        <div className="rb:flex rb:justify-between rb:items-center rb:pb-0">
          <div className="rb:flex rb:items-center rb:gap-4">
            <div className="rb:w-10 rb:h-10 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:rounded-xl rb:flex-shrink-0 rb:overflow-hidden">
              {source.logo_url ? (
                <img 
                  src={source.logo_url} 
                  alt={source.name} 
                  className="rb:w-full rb:h-full rb:object-cover"
                  referrerPolicy="no-referrer"
                  onError={(e) => {
                    e.currentTarget.style.display = 'none';
                    const parent = e.currentTarget.parentElement;
                    if (parent) {
                      parent.innerHTML = '🏪';
                      parent.style.fontSize = '48px';
                    }
                  }}
                />
              ) : (
                <span className="rb:text-5xl">🏪</span>
              )}
            </div>
            <div className="rb:flex rb:items-center rb:flex-1">
              <h2 className="rb:text-xl rb:font-semibold rb:text-gray-900 rb:mb-2 rb:mr-2">{source.name}</h2>
              可用 MCP 服务 <span className="rb:text-gray-600 rb:font-normal">({mcpTotal})</span>
              {/* <p className="rb:text-sm rb:text-gray-600 rb:leading-relaxed">{source.description}</p> */}
            </div>
          </div>

          <div className="rb:flex rb:gap-3">
            <div className="rb:flex rb:gap-3 rb:items-center">
              {source.connected && (
                <Button size="small" icon={<SyncOutlined />} onClick={() => handleRefresh(selectedSource)}>
                  {t('tool.marketRefresh')}
                </Button>
              )}
              {mcpList.length > 0 && (
                <Input
                  prefix={<SearchOutlined />}
                  placeholder={t('tool.marketSearchPlaceholder')}
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  style={{ width: 200 }}
                />
              )}
            </div>
            <Button icon={<SettingOutlined />} onClick={() => handleOpenConfig(selectedSource)}>
              {t('tool.marketConfig')}
            </Button>
            <Button type="primary" icon={<GlobalOutlined />} onClick={() => window.open(source.url, '_blank')}>
              {t('tool.marketVisit')}
            </Button>
          </div>
        </div>

        <div className="rb:mt-6">
          <BodyWrapper loading={loading} empty={mcpList.length === 0}>
            <div id="mcpScrollableDiv" className="rb:overflow-y-auto rb:h-[calc(100vh-260px)]">
              <InfiniteScroll
                dataLength={filteredList.length}
                next={loadMore}
                hasMore={hasMore}
                loader={<Skeleton active paragraph={{ rows: 2 }} className="rb:mt-4" />}
                scrollableTarget="mcpScrollableDiv"
              >
                <div className="rb:grid rb:grid-cols-3 rb:gap-4">
                  {filteredList.map(mcp => (
                  <div 
                    key={mcp.id} 
                    className="rb:bg-white rb:border rb:border-gray-200 rb:rounded-lg rb:p-4 rb:pb-2 rb:transition-all rb:duration-200 hover:rb:shadow-lg hover:rb:border-gray-300"
                  >
                    <div className="rb:flex rb:justify-between rb:items-center rb:mb-3">
                      <div className="rb:w-12 rb:h-12 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:rounded-lg rb:overflow-hidden">
                        {mcp.logo_url ? (
                          <img 
                            src={mcp.logo_url} 
                            alt={getLocaleField(mcp, 'name')} 
                            className="rb:w-full rb:h-full rb:object-cover"
                            referrerPolicy="no-referrer"
                            onError={(e) => {
                              e.currentTarget.style.display = 'none';
                              const parent = e.currentTarget.parentElement;
                              if (parent) {
                                parent.innerHTML = '🔧';
                                parent.style.fontSize = '24px';
                              }
                            }}
                          />
                        ) : (
                          <span className="rb:text-3xl">🔧</span>
                        )}
                      </div>
                      {mcp.categories?.[0] && (
                        <span className="rb:px-2 rb:py-1 rb:rounded rb:text-xs rb:font-medium rb:bg-blue-50 rb:text-blue-700">
                          {mcp.categories[0]}
                        </span>
                      )}
                    </div>
                    <h3 className="rb:text-base rb:font-semibold rb:text-gray-900 rb:mb-1">{getLocaleField(mcp, 'name')}</h3>
                    {mcp.publisher && (
                      <div className="rb:mb-2">
                        <span className="rb:text-xs rb:text-gray-500">{mcp.publisher.startsWith('@') ? mcp.publisher : `@${mcp.publisher}`}</span>
                      </div>
                    )}
                    <p className="rb:text-sm rb:text-gray-600 rb:line-clamp-2 rb:mb-3 rb:min-h-10">{getLocaleField(mcp, 'description')}</p>
                    <div className="rb:flex rb:gap-4 rb:mb-3 rb:pt-3 rb:border-t rb:border-gray-100">
                      {mcp.view_count != null && (
                        <span className="rb:flex rb:items-center rb:gap-1 rb:text-xs rb:text-gray-500">
                          <GlobalOutlined /> {mcp.view_count.toLocaleString()}
                        </span>
                      )}
                    </div>
                    <div className={`rb:flex rb:items-center ${mcp.activated || mcp.inDatabase ? 'rb:justify-between' : 'rb:justify-end'}`}>
                      <div className="rb:flex rb:gap-2">
                        {mcp.activated && <Tag color="success">{t('tool.marketActivated')}</Tag>}
                        {mcp.inDatabase && <Tag color="blue">{t('tool.marketInDatabase')}</Tag>}
                      </div>
                      <Button type="primary" size="small" onClick={() => handleOpenMcpServiceModal(mcp)}>
                        + {t('tool.marketAdd')}
                      </Button>
                    </div>
                  </div>
                ))}
                </div>
              </InfiniteScroll>
            </div>
          </BodyWrapper>
        </div>
      </>
    );
  };

  return (
    <div className="rb:flex rb:gap-4 rb:h-[calc(100vh-138px)]">
      {/* 左侧市场源列表 */}
      <div className="rb:w-80 rb:h-full rb:overflow-y-auto">
        <Space size={12} direction="vertical" className="rb:w-full">
          {categories.map(cat => (
            <Card
              key={cat.id}
              type="inner"
              title={
                <div className="rb:flex rb:items-center rb:gap-2">
                  <span>{cat.name}</span>
                </div>
              }
              classNames={{
                body: "rb:p-[10px]!",
                header: "rb:bg-[#F6F8FC]!"
              }}
            >
              <Space size={8} direction="vertical" className="rb:w-full">
                {marketSources
                  .filter(s => s.category === cat.id)
                  .map(source => (
                    <div
                      key={source.id}
                      className={`rb:bg-white rb:rounded-lg rb:p-2 rb:border rb:cursor-pointer rb:flex rb:items-center rb:gap-2 rb:transition-all ${
                        selectedSource === source.id 
                          ? 'rb:border-[#155EEF] rb:shadow-[0px_2px_4px_0px_rgba(33,35,50,0.15)]' 
                          : 'rb:border-[#DFE4ED] rb:hover:border-[#155EEF] rb:hover:shadow-[0px_2px_4px_0px_rgba(33,35,50,0.15)]'
                      }`}
                      onClick={() => handleSelectSource(source.id)}
                    >
                      <div className="rb:w-5 rb:h-5 rb:flex-shrink-0 rb:flex rb:items-center rb:justify-center rb:overflow-hidden rb:rounded rb:bg-gray-100">
                        {source.logo_url ? (
                          <img 
                            src={source.logo_url} 
                            alt={source.name} 
                            className="rb:w-full rb:h-full rb:object-cover"
                            referrerPolicy="no-referrer"
                            onError={(e) => {
                              e.currentTarget.style.display = 'none';
                              const parent = e.currentTarget.parentElement;
                              if (parent) {
                                parent.innerHTML = '🏪';
                                parent.style.fontSize = '16px';
                              }
                            }}
                          />
                        ) : (
                          <span className="rb:text-base">🏪</span>
                        )}
                      </div>
                      <span className="rb:flex-1 rb:font-medium rb:text-[12px] rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap">
                        {source.name}
                      </span>
                      <span className="rb:text-xs rb:text-gray-500 rb:px-1.5 rb:py-0.5 rb:bg-gray-100 rb:rounded-full rb:flex-shrink-0">
                        {source.mcp_count}
                      </span>
                      {source.connected && (
                        <span className="rb:text-green-500 rb:text-[8px] rb:flex-shrink-0">●</span>
                      )}
                    </div>
                  ))}
              </Space>
            </Card>
          ))}
        </Space>
      </div>

      {/* 右侧内容区 */}
      <div className="rb:flex-1 rb:border-l rb:border-gray-200 rb:overflow-hidden">
        <div className="rb:h-full rb:overflow-y-auto rb:p-6">
          {renderSourceDetail()}
        </div>
      </div>

      {/* 配置弹窗 */}
      <MarketConfigModal
        ref={marketConfigModalRef}
        onConnect={handleConnect}
      />
      <McpServiceModal
        ref={mcpServiceModalRef}
        refresh={() => {}}
      />
    </div>
  );
};

export default Market;
