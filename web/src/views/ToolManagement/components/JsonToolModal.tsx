import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Button, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import type { TreeDataNode } from 'antd';

import type { ToolItem, JsonToolModalRef, ExecuteData } from '../types'
import RbModal from '@/components/RbModal';
import FormItem from 'antd/es/form/FormItem';
import CodeBlock from '@/components/Markdown/CodeBlock';
import { execute } from '@/api/tools';

const JsonToolModal = forwardRef<JsonToolModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ json: string; json_path: string; }>();
  const [data, setData] = useState<ToolItem>({} as ToolItem)
  const [formatValue, setFormatValue] = useState<string | Record<string, any> | null>(null)

  // 转换数据结构为Tree组件需要的格式
  const convertToTreeData = (data: Record<string, any>, parentKey = ''): TreeDataNode[] => {
    if (data.children) {
      return convertToTreeData(data.children, parentKey);
    }
    
    return Object.entries(data).map(([key, item]) => {
      const nodeKey = parentKey ? `${parentKey}-${key}` : key;
      const title = `${key}: ${item.value || ''}`;
      
      const node: TreeDataNode = {
        key: nodeKey,
        title,
      };
      
      if (item.children) {
        node.children = convertToTreeData(item.children, nodeKey);
      }
      
      return node;
    });
  };

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setData({} as ToolItem)
  };

  const handleOpen = (data: ToolItem) => {
    setData(data)
    setVisible(true);
  };
  const handleParse = async () => {
    try {
      const text = await navigator.clipboard.readText();
      form.setFieldValue('json', text);
    } catch (err) {
      console.error('Failed to read clipboard:', err);
    }
  }
  const handleOperate = (type: string) => {
    const json = form.getFieldValue('json')
    const json_path = form.getFieldValue('json_path')
    if (!json || !data.id) return
    let params: ExecuteData = {
      tool_id: data.id,
      parameters: {
        operation: type,
        input_data: json,
        json_path
      }
    }
    if (type === 'parse') {
      params = {
        ...params,
        parameters: {
          ...params.parameters,
        }
      }
    }

    execute(params)
      .then(res => {
        const { data } = res as { data: string; }
        setFormatValue(data);
      })
  }
  const clear = () => {
    form.setFieldValue('json', undefined)
    setFormatValue(null)
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  return (
    <RbModal
      title={data.name}
      open={visible}
      onCancel={handleClose}
      footer={null}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem 
          name="json" 
          label={<Space size={8}>
            {t('tool.enterJson')}
            <Button onClick={clear}>{t('tool.clear')}</Button>
            <Button onClick={handleParse}>{t('tool.paste')}</Button>
          </Space>}
        >
          <Input.TextArea rows={10} placeholder={t('tool.jsonPlaceholder')} />
        </FormItem>
        <FormItem
          name="json_path"
          label={t('tool.json_path')}
        >
          <Input placeholder={t('common.pleaseEnter')} />
        </FormItem>

        <Space size={8} className="rb:mb-3">
          <Button onClick={() => handleOperate('parse')}>{t('tool.parse')}</Button>
        </Space>
        <FormItem
          label={t('tool.outputResult')}
        >
          {typeof formatValue === "string" && formatValue
            ? <CodeBlock value={formatValue} />
            : <div className="rb:bg-[#F0F3F8] rb:text-[12px] rb:p-[16px_20px_16px_24px] rb:rounded-lg rb:text-[#A8A9AA]">{t('tool.noResult')}</div>
          }
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default JsonToolModal;
