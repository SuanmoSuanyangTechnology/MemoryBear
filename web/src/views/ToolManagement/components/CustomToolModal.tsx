import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, App } from 'antd';
import type { UploadFile } from 'antd';
// import UploadImages from '@/components/Upload/UploadImages';
import { getFileLink } from '@/api/fileStorage';
import ToolIcon from '@/assets/images/tool/tool.svg';
import { useTranslation } from 'react-i18next';

import type { CustomToolItem, CustomToolModalRef, ToolItem } from '../types'
import RbModal from '@/components/RbModal';
import { parseSchema, addTool, updateTool } from '@/api/tools';
import Table from '@/components/Table';
import { stringRegExp } from '@/utils/validator';
const FormItem = Form.Item;

interface CustomToolModalProps {
  refresh: () => void;
}

interface CustomToolFormValues extends Omit<CustomToolItem, 'icon'> {
  icon?: UploadFile<{ data?: { file_id?: string } }>;
}

const defaultIcon: NonNullable<CustomToolFormValues['icon']> = {
  uid: 'default-tool-icon',
  name: 'tool.svg',
  status: 'done',
  url: ToolIcon,
};

interface OperationItem {
  method: string;
  path: string;
  summary: string;
  description: string;
  parameters: Record<string, Record<string, string | null>>
  request_body: null | string;
  responses: Record<string, Record<string, string | null>>
  tags: string[]
}
interface ParseSchemaData {
  title: string;
  description: string;
  version: string;
  base_url: string;
  operations: OperationItem[]
}
const authTypeList = ['none', 'api_key', 'basic_auth']
const CustomToolModal = forwardRef<CustomToolModalRef, CustomToolModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<CustomToolFormValues>();
  const authType = Form.useWatch(['config', 'auth_type'], form);
  const [loading, setLoading] = useState(false);
  const [editVo, setEditVo] = useState<ToolItem | null>(null)
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
        icon: data.icon
          ? { uid: data.icon, name: 'icon', status: 'done', url: data.icon }
          : defaultIcon,
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
      .then(({ icon, ...fields }) => {
        setLoading(true);
        const fileId = icon?.response?.data?.file_id;
        const iconUrl = fileId
          ? getFileLink(fileId).then((response: { url: string }) => response.url)
          : Promise.resolve(icon?.url || ToolIcon);

        return iconUrl.then(url => {
          const data: CustomToolItem = { ...fields, icon: url, tool_type: 'custom' };
          return editVo?.id
            ? updateTool(editVo.id, {
              ...editVo,
              ...data,
              config: { ...editVo.config_data, ...data.config },
            })
            : addTool(data);
        }).then(() => {
          message.success(editVo?.id ? t('common.updateSuccess') : t('common.createSuccess'));
          handleClose();
          refresh();
        });
      })
      .finally(() => {
        setLoading(false);
      });
  };
  const formatSchema = (value: string) => {
    if (!value || value.trim() === '') return
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
          // icon: defaultIcon,
          config: {
            auth_type: 'none'
          }
        }}
      >
        <Form.Item
          name="name"
          label={t('tool.name')}
          rules={[
            { required: true, message: t('tool.enterNamePlaceholder') },
            { max: 50 },
            { pattern: stringRegExp, message: t('common.nameInvalid') },
          ]}
        >
          <Input placeholder={t('tool.enterNamePlaceholder')} />
        </Form.Item>
        {/* <Form.Item
          name="icon"
          label={t('application.customIcon')}
          valuePropName="fileList"
          rules={[{
            validator: (_, icon: CustomToolFormValues['icon']) => {
              if (icon && icon.status !== 'done') {
                return Promise.reject(new Error(t('common.pleaseSelect')));
              }
              return Promise.resolve();
            },
          }]}
        >
          <UploadImages fileSize={2} disabled={loading} />
        </Form.Item> */}
        <Form.Item
          name="description"
          label={t('tool.description')}
          rules={[
            { max: 1000 },
          ]}
        >
          <Input.TextArea placeholder={t('common.pleaseEnter')} />
        </Form.Item>
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
          <Table<OperationItem>
            rowKey="summary"
            pagination={false}
            bordered={true}
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
            emptyText={t('tool.toolEmpty')}
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
          {authType === 'api_key' && <>
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
          {authType === 'bearer_token' &&
            <FormItem
              name={['config', 'auth_config', "token"]}
              label={t('tool.bearer_token')}
              rules={[{ required: true, message: t('common.pleaseEnter') }]}
            >
              <Input.Password placeholder={t('common.inputPlaceholder', { title: t('tool.bearer_token') })} />
            </FormItem>
          }

          {/* API Key: 认证方式 = basic_auth 展示 */}
          {authType === 'basic_auth' &&
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
            className="rb:w-full!"
            placeholder={t('tool.tagDesc')}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default CustomToolModal;
