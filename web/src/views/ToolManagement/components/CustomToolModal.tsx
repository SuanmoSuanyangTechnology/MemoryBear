import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Row, Col, App, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { CustomToolItem, CustomToolModalRef, ToolItem } from '../types'
import RbModal from '@/components/RbModal';
import { parseSchema, addTool, updateTool } from '@/api/tools';
import Table from '@/components/Table';
const FormItem = Form.Item;

interface CustomToolModalProps {
  refresh: () => void;
}
interface ParseSchemaData {
  title: string;
  description: string;
  version: string;
  base_url: string;
  operations: Array<{
    method: string;
    path: string;
    summary: string;
    description: string;
    parameters: Record<string, Record<string, string | null>>
    request_body: null | string;
    responses: Record<string, Record<string, string | null>>
    tags: string[]
  }>
}
const authTypeList = ['none', 'api_key', 'basic_auth']
const CustomToolModal = forwardRef<CustomToolModalRef, CustomToolModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<CustomToolItem>();
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ToolItem | null>(null)
  const values = Form.useWatch<CustomToolItem>([], form)
  const [parseSchemaData, setParseSchemaData] = useState<ParseSchemaData>({} as ParseSchemaData)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false);
    setEditVo(null)
    setParseSchemaData({} as ParseSchemaData)
  };

  const handleOpen = (data?: ToolItem) => {
    if (data?.id) {
      const { config_data, ...rest  } = data
      form.setFieldsValue({
        ...rest,
        config: {...config_data}
      })
      setEditVo(data)
      formatSchema(config_data.schema_content)
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
        const { config, ...reset } = values
        const request = editVo?.id ? updateTool(editVo?.id, {
          ...editVo,
          ...reset,
          config: {
            ...editVo.config_data,
            ...config
          }
        }) : addTool({
          ...values,
          tool_type: 'custom'
        })
        request.then(() => {
          message.success(t('tool.addServiceSuccess'));
          handleClose();
          refresh()
        })
          .finally(() => {
            setLoading(false);
          })
      })
      .catch((err) => {
        console.log('表单验证失败:', err);
        setLoading(false);
      });
  };
  const formatSchema = (value: string) => {
    setParseSchemaData({} as ParseSchemaData)
    parseSchema({ schema_content: value })
      .then(res => {
        const response = res as { data: ParseSchemaData }
        setParseSchemaData(response.data)
      })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={editVo?.id ? t('tool.editCustom') : t('tool.addCustom')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
      width={1000}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          config: {
            auth_type: 'none'
          }
        }}
      >
        <Form.Item
          name="name"
          label={t('tool.name')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.pleaseEnter')} />
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
                <Input placeholder={t('common.pleaseEnter')} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Button>icon</Button>
            </Col>
          </Row>
        </Form.Item> */}
        <Form.Item
          name={['config', 'schema_content']}
          label={t('tool.schema')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input.TextArea rows={10} placeholder={t('tool.schemaPlaceholder')} onBlur={(e) => formatSchema(e.target.value)} />
        </Form.Item>
        <Form.Item
          label={t('tool.availableTools')}
        >
          <Table
            rowKey="summary"
            pagination={false}
            columns={[
              {
                title: t('tool.name'),
                dataIndex: 'summary',
                key: 'summary',
                render: (summary) => (
                  <span>{summary ?? parseSchemaData.title}</span>
                )
              },
              {
                title: t('tool.desc'),
                dataIndex: 'description',
                key: 'description',
              },
              {
                title: t('tool.method'),
                dataIndex: 'method',
                key: 'method',
              },
              {
                title: t('tool.path'),
                dataIndex: 'path',
                key: 'path',
              },
            ]}
            initialData={parseSchemaData.operations || []}
            emptySize={88}
          />
        </Form.Item>

        <>
          {/* 认证方式 */}
          <FormItem
            name={['config', 'auth_type']}
            label={t('tool.auth_type')}
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
          {values?.config?.auth_type === 'api_key' && <>
            <FormItem
              name={['config', 'auth_config', "key_name"]}
              label={t('tool.key_name')}
            >
              <Input placeholder={t('common.inputPlaceholder', { title: t('tool.key_name') })} />
            </FormItem>
            <FormItem
              name={['config', 'auth_config', "api_key"]}
              label={t('tool.api_key')}
              rules={[{ required: true, message: t('common.pleaseEnter') }]}
            >
              <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.api_key') })} />
            </FormItem>
          </>}

          {/* API Key: 认证方式 = bearer_token 展示 */}
          {values?.config?.auth_type === 'bearer_token' &&
            <FormItem
              name={['config', 'auth_config', "token"]}
              label={t('tool.bearer_token')}
              rules={[{ required: true, message: t('common.pleaseEnter') }]}
            >
              <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.bearer_token') })} />
            </FormItem>
          }

          {/* API Key: 认证方式 = basic_auth 展示 */}
          {values?.config?.auth_type === 'basic_auth' &&
            <>
              <FormItem
                name={['config', 'auth_config', "username"]}
                label={t('tool.username')}
                rules={[{ required: true, message: t('common.pleaseEnter') }]}
              >
                <Input placeholder={t('common.inputPlaceholder', { title: t('tool.username') })} />
              </FormItem>
              <FormItem
                name={['config', 'auth_config', "password"]}
                label={t('tool.password')}
                rules={[{ required: true, message: t('common.pleaseEnter') }]}
              >
                <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.password') })} />
              </FormItem>
            </>
          }
        </>
        <FormItem
          name="tags"
          label={t('tool.tags')}
          extra={t('tool.tagDesc')}
        >
          <Select
            mode="tags"
            style={{ width: '100%' }}
            placeholder={t('common.pleaseEnter')}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default CustomToolModal;
