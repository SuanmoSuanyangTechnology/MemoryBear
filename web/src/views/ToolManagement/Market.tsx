import React, { useState, useRef, type ReactNode } from 'react';
import { Input, Button, Spin, App } from 'antd';
import { SearchOutlined, SettingOutlined, GlobalOutlined, SyncOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import MarketConfigModal, { type MarketConfigModalRef } from './components/MarketConfigModal';

interface MarketSource {
  id: string;
  name: string;
  category: string;
  icon: string;
  url: string;
  desc: string;
  apiKey: string;
  connected: boolean;
  mcpCount: number;
}

interface MarketMcp {
  id: string;
  name: string;
  provider: string;
  type: string;
  desc: string;
  downloads?: string;
  stars?: string;
  icon: string;
  configTemplate: any;
}

interface MarketCategory {
  id: string;
  name: string;
  icon: string;
}

const Market: React.FC<{ getStatusTag?: (status: string) => ReactNode }> = () => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const marketConfigModalRef = useRef<MarketConfigModalRef>(null);
  const [marketSources, setMarketSources] = useState<MarketSource[]>([
    { id: 'smithery', name: 'Smithery', category: 'official', icon: '🔧', url: 'https://mcp.smithery.ai', desc: '官方 MCP 服务市场，提供丰富的 MCP 服务', apiKey: '', connected: false, mcpCount: 2847 },
    { id: 'mcpmarket', name: 'MCP Market', category: 'official', icon: '🏪', url: 'https://mcpmarket.com', desc: '综合性 MCP 市场平台', apiKey: '', connected: false, mcpCount: 1523 },
    { id: 'glama', name: 'Glama.ai MCP', category: 'official', icon: '✨', url: 'https://glama.ai/mcp', desc: 'Glama AI 提供的 MCP 服务集合', apiKey: '', connected: false, mcpCount: 892 },
    { id: 'github-mcp', name: 'modelcontextprotocol/servers', category: 'official', icon: '🐙', url: 'https://github.com/modelcontextprotocol/servers', desc: 'GitHub 官方 MCP 服务器仓库', apiKey: '', connected: true, mcpCount: 156 },
    { id: 'aliyun-bailian', name: '阿里云百炼 MCP', category: 'china-cloud', icon: '☁️', url: 'https://bailian.console.aliyun.com/mcp', desc: '阿里云百炼平台 MCP 市场', apiKey: '', connected: false, mcpCount: 423 },
    { id: 'modelscope', name: '魔搭社区 MCP', category: 'china-cloud', icon: '🎭', url: 'https://modelscope.cn/mcp', desc: '阿里达摩院魔搭社区 MCP 市场', apiKey: '', connected: false, mcpCount: 312 },
  ]);

  const [categories] = useState<MarketCategory[]>([
    { id: 'official', name: '官方/综合', icon: '🌐' },
    { id: 'china-cloud', name: '国内云', icon: '☁️' },
    { id: 'community', name: '社区/垂直', icon: '👥' }
  ]);

  const [mcpCache, setMcpCache] = useState<Record<string, MarketMcp[]>>({
    'github-mcp': [
      { id: 'gh-1', name: 'Fetch', provider: 'modelcontextprotocol', type: 'Hosted', desc: '使用浏览器模拟大型语言模型检索和处理网页内容', downloads: '203.7m', stars: '308.2k', icon: '🌐', configTemplate: {} },
      { id: 'gh-2', name: 'Filesystem', provider: 'modelcontextprotocol', type: 'Local', desc: '安全的文件系统操作，支持读写文件和目录管理', downloads: '156.2m', stars: '245.1k', icon: '📁', configTemplate: {} },
      { id: 'gh-3', name: 'GitHub', provider: 'modelcontextprotocol', type: 'Hosted', desc: 'GitHub API 集成，支持仓库、Issue、PR 等操作', downloads: '89.4m', stars: '178.3k', icon: '🐙', configTemplate: {} },
    ]
  });

  const [searchKeyword, setSearchKeyword] = useState('');

  const handleSelectSource = (sourceId: string) => {
    setSelectedSource(sourceId);
  };

  const handleRefresh = (sourceId: string) => {
    setLoading(true);
    setTimeout(() => {
      // 模拟刷新数据
      const source = marketSources.find(s => s.id === sourceId);
      if (source) {
        message.success(`${source.name} 列表已刷新`);
      }
      setLoading(false);
    }, 600);
  };

  const handleOpenConfig = (sourceId: string) => {
    const source = marketSources.find(s => s.id === sourceId);
    if (source) {
      marketConfigModalRef.current?.handleOpen(source);
    }
  };

  const handleConnect = (sourceId: string, apiKey: string) => {
    // 更新市场源状态
    setMarketSources(prev => prev.map(source => {
      if (source.id === sourceId) {
        return {
          ...source,
          apiKey,
          connected: true
        };
      }
      return source;
    }));

    // 模拟获取MCP列表
    setTimeout(() => {
      const source = marketSources.find(s => s.id === sourceId);
      if (source && !mcpCache[sourceId]) {
        // 生成模拟数据
        const mockData: MarketMcp[] = [
          { id: `${sourceId}-1`, name: `${source.name} 服务 1`, provider: source.name, type: 'Hosted', desc: `来自 ${source.name} 的 MCP 服务`, downloads: '10.2m', stars: '23.4k', icon: '🔧', configTemplate: {} },
          { id: `${sourceId}-2`, name: `${source.name} 服务 2`, provider: source.name, type: 'Local', desc: `来自 ${source.name} 的本地 MCP 服务`, downloads: '8.5m', stars: '18.7k', icon: '⚙️', configTemplate: {} }
        ];
        setMcpCache(prev => ({
          ...prev,
          [sourceId]: mockData
        }));
      }
      message.success(`已连接 ${source?.name}`);
    }, 800);
  };

  const renderSourceDetail = () => {
    if (!selectedSource) {
      return (
        <div className="rb:flex rb:flex-col rb:items-center rb:justify-center rb:h-full rb:text-center">
          <div className="rb:text-6xl rb:mb-4">🏪</div>
          <h3 className="rb:text-lg rb:font-semibold rb:text-gray-900 rb:mb-2">选择一个 MCP 市场</h3>
          <p className="rb:text-sm rb:text-gray-600 rb:max-w-md">从左侧选择一个市场源，配置连接后即可浏览该市场的 MCP 服务</p>
        </div>
      );
    }

    const source = marketSources.find(s => s.id === selectedSource);
    if (!source) return null;

    const mcpList = mcpCache[selectedSource] || [];
    const filteredList = mcpList.filter(mcp => 
      mcp.name.toLowerCase().includes(searchKeyword.toLowerCase()) ||
      mcp.desc.toLowerCase().includes(searchKeyword.toLowerCase())
    );

    return (
      <>
        <div className="rb:flex rb:justify-between rb:items-start rb:pb-6 rb:border-b rb:border-gray-200 rb:mb-6">
          <div className="rb:flex rb:gap-4">
            <div className="rb:text-5xl rb:w-16 rb:h-16 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:rounded-xl rb:flex-shrink-0">
              {source.icon}
            </div>
            <div className="rb:flex-1">
              <h2 className="rb:text-xl rb:font-semibold rb:text-gray-900 rb:mb-2">{source.name}</h2>
              <p className="rb:text-sm rb:text-gray-600 rb:leading-relaxed">{source.desc}</p>
            </div>
          </div>
          <div className="rb:flex rb:gap-3">
            <Button icon={<SettingOutlined />} onClick={() => handleOpenConfig(selectedSource)}>
              配置
            </Button>
            <Button type="primary" icon={<GlobalOutlined />} onClick={() => window.open(source.url, '_blank')}>
              前往市场
            </Button>
          </div>
        </div>

        <div className="rb:mt-6">
          <div className="rb:flex rb:justify-between rb:items-center rb:mb-5">
            <h3 className="rb:text-base rb:font-semibold rb:text-gray-900 rb:m-0">
              可用 MCP 服务 <span className="rb:text-gray-600 rb:font-normal">({mcpList.length})</span>
            </h3>
            <div className="rb:flex rb:gap-3 rb:items-center">
              {source.connected && (
                <Button size="small" icon={<SyncOutlined />} onClick={() => handleRefresh(selectedSource)}>
                  刷新
                </Button>
              )}
              {mcpList.length > 0 && (
                <Input
                  prefix={<SearchOutlined />}
                  placeholder="搜索服务..."
                  value={searchKeyword}
                  onChange={(e) => setSearchKeyword(e.target.value)}
                  style={{ width: 200 }}
                />
              )}
            </div>
          </div>

          {mcpList.length > 0 ? (
            <Spin spinning={loading}>
              <div className="rb:grid rb:grid-cols-1 md:rb:grid-cols-2 lg:rb:grid-cols-3 rb:gap-4">
                {filteredList.map(mcp => (
                  <div 
                    key={mcp.id} 
                    className="rb:bg-white rb:border rb:border-gray-200 rb:rounded-lg rb:p-4 rb:transition-all rb:duration-200 hover:rb:shadow-lg hover:rb:border-gray-300"
                  >
                    <div className="rb:flex rb:justify-between rb:items-center rb:mb-3">
                      <div className="rb:text-3xl rb:w-12 rb:h-12 rb:flex rb:items-center rb:justify-center rb:bg-gray-50 rb:rounded-lg">
                        {mcp.icon}
                      </div>
                      <span className={`rb:px-2 rb:py-1 rb:rounded rb:text-xs rb:font-medium ${
                        mcp.type === 'Hosted' 
                          ? 'rb:bg-blue-50 rb:text-blue-700' 
                          : 'rb:bg-gray-100 rb:text-gray-600'
                      }`}>
                        {mcp.type}
                      </span>
                    </div>
                    <h3 className="rb:text-base rb:font-semibold rb:text-gray-900 rb:mb-1">{mcp.name}</h3>
                    {mcp.provider && (
                      <div className="rb:mb-2">
                        <span className="rb:text-xs rb:text-gray-500">@ {mcp.provider}</span>
                      </div>
                    )}
                    <p className="rb:text-sm rb:text-gray-600 rb:leading-relaxed rb:mb-3 rb:min-h-[42px]">{mcp.desc}</p>
                    <div className="rb:flex rb:gap-4 rb:mb-3 rb:pt-3 rb:border-t rb:border-gray-100">
                      {mcp.downloads && (
                        <span className="rb:flex rb:items-center rb:gap-1 rb:text-xs rb:text-gray-500">
                          <GlobalOutlined /> {mcp.downloads}
                        </span>
                      )}
                      {mcp.stars && (
                        <span className="rb:flex rb:items-center rb:gap-1 rb:text-xs rb:text-gray-500">
                          ⭐ {mcp.stars}
                        </span>
                      )}
                    </div>
                    <div className="rb:flex rb:justify-end">
                      <Button type="primary" size="small">
                        + 添加
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </Spin>
          ) : (
            <div className="rb:flex rb:flex-col rb:items-center rb:justify-center rb:py-16 rb:text-center">
              <div className="rb:text-6xl rb:mb-4">{source.connected ? '📭' : '🔌'}</div>
              <h4 className="rb:text-base rb:font-semibold rb:text-gray-900 rb:mb-2">
                {source.connected ? '暂无可用的 MCP 服务' : '尚未连接此市场'}
              </h4>
              <p className="rb:text-sm rb:text-gray-600 rb:mb-4">
                {source.connected ? '该市场暂时没有可用的服务' : '点击右上角"配置"按钮设置连接信息'}
              </p>
              {!source.connected && (
                <Button type="primary" onClick={() => handleOpenConfig(selectedSource)}>
                  配置连接
                </Button>
              )}
            </div>
          )}
        </div>
      </>
    );
  };

  return (
    <div className="rb:flex rb:gap-4 rb:h-[calc(100vh-178px)]">
      {/* 左侧市场源列表 */}
      <div className="rb:w-70 rb:bg-white rb:rounded-lg rb:border rb:border-gray-200 rb:overflow-y-auto rb:flex-shrink-0">
        <div className="rb:p-4 rb:border-b rb:border-gray-200">
          <span className="rb:text-base rb:font-semibold rb:text-gray-900">MCP 市场</span>
        </div>
        {categories.map(cat => (
          <div key={cat.id} className="rb:py-3 rb:border-b rb:border-gray-100 last:rb:border-b-0">
            <div className="rb:flex rb:items-center rb:gap-2 rb:px-4 rb:py-2 rb:text-xs rb:font-medium rb:text-gray-500 rb:uppercase">
              <span className="rb:text-sm">{cat.icon}</span>
              <span>{cat.name}</span>
            </div>
            <div className="rb:px-2 rb:py-1">
              {marketSources
                .filter(s => s.category === cat.id)
                .map(source => (
                  <div
                    key={source.id}
                    className={`rb:flex rb:items-center rb:gap-2 rb:px-3 rb:py-2.5 rb:rounded-md rb:cursor-pointer rb:transition-all rb:relative ${
                      selectedSource === source.id 
                        ? 'rb:bg-blue-50 rb:text-blue-600' 
                        : 'hover:rb:bg-gray-50'
                    }`}
                    onClick={() => handleSelectSource(source.id)}
                  >
                    <span className="rb:text-lg rb:flex-shrink-0">{source.icon}</span>
                    <span className="rb:flex-1 rb:text-sm rb:font-medium rb:overflow-hidden rb:text-ellipsis rb:whitespace-nowrap">
                      {source.name}
                    </span>
                    <span className="rb:text-xs rb:text-gray-500 rb:px-1.5 rb:py-0.5 rb:bg-gray-100 rb:rounded-full">
                      {source.mcpCount}
                    </span>
                    {source.connected && (
                      <span className="rb:text-green-500 rb:text-[8px] rb:ml-1">●</span>
                    )}
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>

      {/* 右侧内容区 */}
      <div className="rb:flex-1 rb:bg-white rb:rounded-lg rb:border rb:border-gray-200 rb:overflow-hidden">
        <div className="rb:h-full rb:overflow-y-auto rb:p-6">
          {renderSourceDetail()}
        </div>
      </div>

      {/* 配置弹窗 */}
      <MarketConfigModal
        ref={marketConfigModalRef}
        onConnect={handleConnect}
      />
    </div>
  );
};

export default Market;
