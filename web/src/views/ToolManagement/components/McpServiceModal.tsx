import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Form, Input, Select, App, Button, Tabs, Space } from 'antd';
import { useTranslation } from 'react-i18next';

import type { MCPToolItem, ToolItem } from '../types'
import RbModal from '@/components/RbModal';
import Empty from '@/components/Empty';
import RequestHeaderModal from './RequestHeaderModal';
import Table from '@/components/Table';
import { addTool, updateTool, testConnection } from '@/api/tools'
import type { McpServiceModalRef } from '../types'

const FormItem = Form.Item;

interface McpServiceModalProps {
  refresh: () => void;
}

export interface RequestHeader { 
  key: string; 
  value: string;
  [key: string]: string | undefined;
}
export interface RequestHeaderModalRef {
  handleOpen: (index?: number, data?: RequestHeader) => void;
  handleClose: () => void;
}
const authTypeList = ['none', 'api_key', 'basic_auth', 'bearer_token']
const tabKeys = ['auth', 'requestHeader', 'config']
const McpServiceModal = forwardRef<McpServiceModalRef, McpServiceModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<MCPToolItem>();
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ToolItem | null>(null)
  const [activeTab, setActiveTab] = useState('auth');
  const values = Form.useWatch<MCPToolItem>([], form)
  const requestHeaderModalRef = useRef<RequestHeaderModalRef>(null)
  const [requestHeaderList, setRequestHeaderList] = useState<RequestHeader[]>([])

  const formatTabItems = () => {
    return tabKeys.map(key => ({
      key,
      label: t(`tool.${key}`),
    }))
  }
  const handleChangeTab = (key: string) => {
    setActiveTab(key);
  }

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditVo(null)
    setActiveTab('auth')
    setRequestHeaderList([])
  };

  const handleOpen = (data?: ToolItem) => {
    if (data?.id) {
      const { config_data, name, description, icon } = data
      form.setFieldsValue({
        name, description, icon,
        config: { ...config_data }
      })

      if (config_data.connection_config.headers) {
        console.log(Object.keys(config_data.connection_config.headers).map(key => ({
          key,
          value: config_data.connection_config.headers[key]
        })))
        setRequestHeaderList(Object.keys(config_data.connection_config.headers).map(key => ({
          key,
          value: config_data.connection_config.headers[key]
        })))
      }
      setEditVo(data)
    } else {
      form.resetFields();
    }
    setVisible(true);
  };

  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true);
        // 创建新服务对象
        const { config, ...rest } = values

        const newService: MCPToolItem = {
          ...rest,
          tool_type: 'mcp',
          config: {
            ...config,
            connection_config: {
              ...config.connection_config,
              headers: requestHeaderList.reduce((acc: Record<string, string>, cur) => {
                acc[cur.key] = cur.value
                return acc
              }, {})
            }
          }
        }
        const request = editVo?.id ? updateTool(editVo.id, newService) : addTool(newService)
        request.then((res: any) => {
          message.success(t('common.saveSuccess'));
          testConnection(res.tool_id || editVo?.id)
            .then(() => {
              handleClose();
              refresh()
            })
            .finally(() => {
              setLoading(false);
            })
        })
        .catch(() => {
          setLoading(false);
        })
      })
      .catch((err) => {
        console.log('表单验证失败:', err);
        setLoading(false);
      });
  };
  const handleEditRequestHeader = (index?: number, data?: RequestHeader) => {
    requestHeaderModalRef.current?.handleOpen(index, data)
  }
  const handleDeleteRequestHeader  = (index: number) => {
    const list = requestHeaderList.filter((_item, idx) => idx !== index)
    setRequestHeaderList([...list])
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={editVo?.id ? t('tool.editService') : `${t('tool.addService')} (HTTP)`}
      open={visible}
      onCancel={handleClose}
      okText={t('tool.saveAndTest')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          config: {
            connection_config: {
              auth_type: 'none',
              timeout: 30,
            },
          }
        }}
      >
        {/* 服务端点 URL */}
        <FormItem
          name={['config', "server_url"]}
          label={t('tool.serviceEndpoint')}
          extra={t('tool.serviceEndpointExtra')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('tool.serviceEndpointPlaceholder')} />
        </FormItem>
        <Form.Item
          name="name"
          label={t('tool.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('tool.namePlaceholder')} />
        </Form.Item>
        {/* 名称和图标 */}
        {/* <Form.Item label={t('tool.nameAndIcon')} required>
          <Row gutter={8}>
            <Col span={16}>
              <Form.Item
                name="name"
                noStyle
                rules={[{ required: true, message: t('common.pleaseEnter') }]}
              >
                <Input placeholder={t('tool.namePlaceholder')} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Button>icon</Button>
            </Col>
          </Row>
        </Form.Item> */}

        {/* 描述 */}
        <FormItem
          name="description"
          label={t('tool.description')}
        >
          <Input.TextArea rows={3} placeholder={t('common.inputPlaceholder', { title: t('tool.description') })}/>
        </FormItem>


        {/* 认证、请求头、配置 */}
        <Tabs 
          activeKey={activeTab} 
          items={formatTabItems()} 
          onChange={handleChangeTab}
        />
        {/* 认证模块 */}
        <>
          {/* 认证方式 */}
          <FormItem
            name={['config', 'connection_config', 'auth_type']}
            label={t('tool.auth_type')}
            hidden={activeTab !== 'auth'}
          >
            <Select
              placeholder={t('common.pleaseSelect')}
              options={authTypeList.map(value => ({
                label: t(`tool.${value}`),
                value
              }))}
            />
          </FormItem>

          {/* API Key: 认证方式 = api_key 展示 */}
          {values?.config?.connection_config?.auth_type === 'api_key' && <>
            <FormItem
              name={['config', 'connection_config', 'auth_config', "key_name"]}
              label={t('tool.key_name')}
              hidden={activeTab !== 'auth'}
            >
              <Input placeholder={t('common.inputPlaceholder', { title: t('tool.key_name') })} />
            </FormItem>
            <FormItem
              name={['config', 'connection_config', 'auth_config', "api_key"]}
              label={t('tool.api_key')}
              hidden={activeTab !== 'auth'}
              rules={[{ required: true, message: t('common.pleaseEnter') }]}
            >
              <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.api_key') })} />
            </FormItem>
          </>}

          {/* API Key: 认证方式 = bearer_token 展示 */}
          {values?.config?.connection_config?.auth_type === 'bearer_token' &&
            <FormItem
              name={['config', 'connection_config', 'auth_config', "token"]}
              label={t('tool.bearer_token')}
              hidden={activeTab !== 'auth'}
              rules={[{ required: true, message: t('common.pleaseEnter') }]}
            >
              <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.bearer_token') })} />
            </FormItem>
          }

          {/* API Key: 认证方式 = basic_auth 展示 */}
          {values?.config?.connection_config?.auth_type === 'basic_auth' &&
            <>
              <FormItem
                name={['config', 'connection_config', 'auth_config', "username"]}
                label={t('tool.username')}
                hidden={activeTab !== 'auth'}
                rules={[{ required: true, message: t('common.pleaseEnter') }]}
              >
                <Input placeholder={t('common.inputPlaceholder', { title: t('tool.username') })} />
              </FormItem>
              <FormItem
                name={['config', 'connection_config', 'auth_config', "password"]}
                label={t('tool.password')}
                hidden={activeTab !== 'auth'}
                rules={[{ required: true, message: t('common.pleaseEnter') }]}
              >
                <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.password') })} />
              </FormItem>
            </>
          }
        </>
        {/* 请求头模块 */}
        <div className={activeTab !== 'requestHeader' ? 'rb:hidden' : ''}>
          <div className="rb:flex rb:items-center rb:justify-between rb:mb-1 rb:w-full">
            <div className="rb:font-medium rb:leading-5">{t('tool.requestHeader')}</div>
            <Button style={{padding: '0 8px', height: '24px'}} onClick={() => handleEditRequestHeader()}>+{t('tool.addRequestHeader')}</Button>
          </div>
          <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4 rb:mb-3">{t('tool.requestHeaderDesc')}</div>

          {requestHeaderList.length === 0
            ? <Empty size={88} />
            : 
            <Table
              rowKey="key"
              pagination={false}
              columns={[
                {
                  title: t('tool.requestHeaderName'),
                  dataIndex: 'key',
                  key: 'key',
                  width: 120,
                },
                {
                  title: t('tool.requestHeaderValue'),
                  dataIndex: 'value',
                  key: 'value',
                  render: (value) => {
                    return <div className="rb:break-all">{value}</div>
                  }
                },
                {
                  title: t('common.operation'),
                  key: 'action',
                  width: 80,
                  render: (_, record, index: number) => (
                    <Space size="middle">
                      <Button
                        type="link"
                        onClick={() => handleEditRequestHeader(index, record as RequestHeader)}
                      >
                        {t('common.edit')}
                      </Button>
                      <Button type="link" danger onClick={() => handleDeleteRequestHeader(index)}>
                        {t('common.delete')}
                      </Button>
                    </Space>
                  ),
                },
              ]}
              initialData={requestHeaderList}
              emptySize={88}
              scroll={{ x: 'max-content' }}
            />
          }
        </div>
        {/* 配置模块 */}
        <>
          <FormItem
            name={['config', 'connection_config', "timeout"]}
            label={t('tool.timeout')}
            hidden={activeTab !== 'config'}
          >
            <Input type="number" min={5} max={300} placeholder={t('common.pleaseEnter')} />
          </FormItem>
        </>
      </Form>

      <RequestHeaderModal
        ref={requestHeaderModalRef}
        refreshTable={setRequestHeaderList}
      />
    </RbModal>
  );
});

export default McpServiceModal;
