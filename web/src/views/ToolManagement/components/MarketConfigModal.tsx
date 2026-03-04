import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Button, App, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { CopyOutlined, EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons';
import RbModal from '@/components/RbModal';

const FormItem = Form.Item;

interface MarketSource {
  id: string;
  name: string;
  icon: string;
  url: string;
  desc: string;
  apiKey: string;
  connected: boolean;
}

interface MarketConfigModalProps {
  onConnect: (sourceId: string, apiKey: string) => void;
}

export interface MarketConfigModalRef {
  handleOpen: (source: MarketSource) => void;
  handleClose: () => void;
}

const MarketConfigModal = forwardRef<MarketConfigModalRef, MarketConfigModalProps>(({
  onConnect
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [currentSource, setCurrentSource] = useState<MarketSource | null>(null);
  const [showApiKey, setShowApiKey] = useState(false);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setCurrentSource(null);
    setShowApiKey(false);
  };

  const handleOpen = (source: MarketSource) => {
    setCurrentSource(source);
    form.setFieldsValue({
      url: source.url,
      apiKey: source.apiKey,
    });
    setVisible(true);
  };

  const handleSave = () => {
    form
      .validateFields()
      .then((values) => {
        if (!currentSource) return;
        
        setLoading(true);
        
        // 模拟连接延迟
        setTimeout(() => {
          onConnect(currentSource.id, values.apiKey || '');
          message.success(`正在连接 ${currentSource.name}...`);
          setLoading(false);
          handleClose();
        }, 500);
      })
      .catch((err) => {
        console.log('表单验证失败:', err);
      });
  };

  const handleCopyUrl = () => {
    if (currentSource?.url) {
      navigator.clipboard.writeText(currentSource.url).then(() => {
        message.success(t('common.copySuccess'));
      });
    }
  };

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  if (!currentSource) return null;

  return (
    <RbModal
      title={`配置 ${currentSource.name}`}
      open={visible}
      onCancel={handleClose}
      okText="保存并连接"
      onOk={handleSave}
      confirmLoading={loading}
      width={600}
    >
      <div>
        {/* 市场源信息头部 */}
        <div className="rb:flex rb:gap-4 rb:mb-6 rb:p-4 rb:bg-gray-50 rb:rounded-lg">
          <div className="rb:text-4xl rb:w-16 rb:h-16 rb:flex rb:items-center rb:justify-center rb:bg-white rb:rounded-lg rb:flex-shrink-0">
            {currentSource.icon}
          </div>
          <div className="rb:flex-1">
            <h3 className="rb:text-base rb:font-semibold rb:mb-1 rb:text-gray-900">{currentSource.name}</h3>
            <p className="rb:text-sm rb:text-gray-600 rb:leading-relaxed">{currentSource.desc}</p>
          </div>
        </div>

        <Form
          form={form}
          layout="vertical"
        >
          {/* 市场地址 */}
          <FormItem
            name="url"
            label="市场地址"
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                readOnly
                placeholder="市场地址"
              />
              <Button
                icon={<CopyOutlined />}
                onClick={handleCopyUrl}
              >
                复制
              </Button>
            </Space.Compact>
          </FormItem>

          {/* API Key */}
          <FormItem
            name="apiKey"
            label={
              <span>
                API Key <span className="rb:text-gray-400 rb:font-normal">(可选)</span>
              </span>
            }
            extra="部分市场需要 API Key 才能获取完整的服务列表"
          >
            <Space.Compact style={{ width: '100%' }}>
              <Input
                type={showApiKey ? 'text' : 'password'}
                placeholder="输入 API Key 以获取更多服务"
                autoComplete="off"
              />
              <Button
                icon={showApiKey ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                onClick={() => setShowApiKey(!showApiKey)}
              />
            </Space.Compact>
          </FormItem>

          {/* 连接状态 */}
          <div className="rb:flex rb:items-center rb:gap-2 rb:p-3 rb:bg-gray-50 rb:rounded rb:text-sm">
            <span className="rb:text-gray-600">连接状态：</span>
            <span className={`rb:font-medium ${currentSource.connected ? 'rb:text-green-600' : 'rb:text-gray-400'}`}>
              {currentSource.connected ? '● 已连接' : '○ 未连接'}
            </span>
          </div>
        </Form>
      </div>
    </RbModal>
  );
});

export default MarketConfigModal;
