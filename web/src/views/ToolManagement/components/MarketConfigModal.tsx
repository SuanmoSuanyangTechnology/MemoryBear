import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Button, App, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import { CopyOutlined, EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons';
import { createMarketConfig,updateMarketConfig } from '@/api/tools';
import RbModal from '@/components/RbModal';

const FormItem = Form.Item;

interface MarketSource {
  id: string;
  name: string;
  logo_url: string;
  url: string;
  description: string;
  token?: string;
  connected: boolean;
  configId?: string;
}

interface MarketConfigModalProps {
  onConnect: (sourceId: string, configId: string) => void;
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
  const [initialValues, setInitialValues] = useState<{ token: string }>({ token: '' });
  const formValues = Form.useWatch([], form);

  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setCurrentSource(null);
    setShowApiKey(false);
    setInitialValues({ token: '' });
  };

  const handleOpen = (source: MarketSource) => {
    console.log('Modal 接收到的数据:', source);
    setCurrentSource(source);
    setInitialValues({ token: source.token || '' });
    setVisible(true);
  };

  const handleAfterOpenChange = (open: boolean) => {
    if (open && currentSource) {
      // Modal 完全打开后再设置表单值，使用 setTimeout 确保在下一个事件循环
      setTimeout(() => {
        form.setFieldsValue({
          token: currentSource.token || '',
        });
        console.log('Modal 打开后设置表单值:', { token: currentSource.token || '' });
        console.log('当前表单所有值:', form.getFieldsValue());
      }, 100);
    }
  };

  const handleSave = () => {
    form
      .validateFields()
      .then(async (values) => {
        if (!currentSource) return;
        
        setLoading(true);
        try {
          let res: any;
          if (currentSource.configId) {
            // 更新配置
            res = await updateMarketConfig({
              mcp_market_config_id: currentSource.configId,
              token: values.token || '',
              status: 1,
            });
            message.success(t('tool.marketConfigUpdated', { name: currentSource.name }));
          } else {
            // 创建配置
            res = await createMarketConfig({
              mcp_market_id: currentSource.id || '',
              token: values.token || '',
              status: 1,
            });
            message.success(t('tool.marketConnecting', { name: currentSource.name }));
          }
          onConnect(currentSource.id, res.id || currentSource.configId);
          handleClose();
        } catch (error) {
          console.error('保存配置失败:', error);
        } finally {
          setLoading(false);
        }
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

  // 检查是否可以保存：token 字段必须有值
  const canSave = formValues?.token?.trim().length > 0;

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  if (!currentSource) return null;

  return (
    <RbModal
      title={t('tool.marketConfig', { name: currentSource.name })}
      open={visible}
      onCancel={handleClose}
      afterOpenChange={handleAfterOpenChange}
      okText={t('tool.marketSaveAndConnect')}
      onOk={handleSave}
      confirmLoading={loading}
      okButtonProps={{ disabled: !canSave }}
      width={600}
    >
      <div>
        {/* 市场源信息头部 */}
        <div className="rb:flex rb:gap-4 rb:mb-6 rb:p-4 rb:bg-gray-50 rb:rounded-lg">
          <div className="rb:w-16 rb:h-16 rb:flex rb:items-center rb:justify-center rb:bg-white rb:rounded-lg rb:flex-shrink-0 rb:overflow-hidden">
            {currentSource.logo_url ? (
              <img 
                src={currentSource.logo_url} 
                alt={currentSource.name} 
                className="rb:w-full rb:h-full rb:object-cover"
                onError={(e) => {
                  e.currentTarget.style.display = 'none';
                  const parent = e.currentTarget.parentElement;
                  if (parent) {
                    parent.innerHTML = '🏪';
                    parent.style.fontSize = '32px';
                  }
                }}
              />
            ) : (
              <span className="rb:text-4xl">🏪</span>
            )}
          </div>
          <div className="rb:flex-1">
            <h3 className="rb:text-base rb:font-semibold rb:mb-1 rb:text-gray-900">{currentSource.name}</h3>
            <p className="rb:text-sm rb:text-gray-600 rb:leading-relaxed">{currentSource.description}</p>
          </div>
        </div>

        <Form
          key={currentSource?.id || 'new'}
          form={form}
          layout="vertical"
          initialValues={initialValues}
        >
          <FormItem label={t('tool.marketUrl')}>
            <Space.Compact style={{ width: '100%' }}>
              <Input
                readOnly
                value={currentSource.url}
              />
              <Button
                icon={<CopyOutlined />}
                onClick={handleCopyUrl}
              >
                {t('tool.marketCopy')}
              </Button>
            </Space.Compact>
          </FormItem>

          <FormItem
            name="token"
            label={
              <span>
                API Key
              </span>
            }
            rules={[
              { required: true, message: t('tool.marketApiKeyRequired') },
              { whitespace: true, message: t('tool.marketApiKeyRequired') }
            ]}
            extra={<span style={{ display: 'inline-block', marginTop: 8 }}>{t('tool.marketApiKeyExtra')}</span>}
          >
            <Input
              type={showApiKey ? 'text' : 'password'}
              placeholder={t('tool.marketApiKeyPlaceholder')}
              autoComplete="off"
              suffix={
                <Button
                  type="text"
                  size="small"
                  icon={showApiKey ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                  onClick={() => setShowApiKey(!showApiKey)}
                />
              }
            />
          </FormItem>

          <div className="rb:flex rb:items-center rb:gap-2 rb:p-3 rb:bg-gray-50 rb:rounded rb:text-sm">
            <span className="rb:text-gray-600">{t('tool.marketConnectionStatus')}：</span>
            <span className={`rb:font-medium ${currentSource.connected ? 'rb:text-green-600' : 'rb:text-gray-400'}`}>
              {currentSource.connected ? t('tool.marketConnected') : t('tool.marketDisconnected')}
            </span>
          </div>
        </Form>
      </div>
    </RbModal>
  );
});

export default MarketConfigModal;
