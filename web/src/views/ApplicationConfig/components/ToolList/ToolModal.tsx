/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:06 
 */
/**
 * Tool Selection Modal
 * Provides cascading selection of tools by type, tool, and method
 * Supports MCP, builtin, and custom tool types
 */

import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Cascader, type CascaderProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolModalRef, ToolOption } from './types'
import RbModal from '@/components/RbModal'
import { getToolMethods, getTools } from '@/api/tools'
import type { ToolType, ToolItem } from '@/views/ToolManagement/types'

const FormItem = Form.Item;

/**
 * Component props
 */
interface ToolModalProps {
  /** Callback to add selected tool */
  refresh: (tool: ToolOption) => void;
}

/**
 * Modal for selecting tools
 */
const ToolModal = forwardRef<ToolModalRef, ToolModalProps>(({
  refresh,
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false)
  const [optionList, setOptionList] = useState<ToolOption[]>([
    { value: 'mcp', label: t('tool.mcp'), isLeaf: false },
    { value: 'builtin', label: t('tool.inner'), isLeaf: false },
    { value: 'custom', label: t('tool.custom'), isLeaf: false },
  ])
  const [selectdTools, setSelectedTools] = useState<ToolOption[]>([])

  /** Close modal and reset state */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setSelectedTools([])
  };

  /** Open modal */
  const handleOpen = () => {
    setVisible(true);
    form.resetFields();
    setSelectedTools([])
  };
  /** Save selected tool */
  const handleSave = () => {
    form.validateFields().then(() => {
      setLoading(false)
      let operation: any = undefined
      if (selectdTools[0].value === 'mcp' || (selectdTools[0].value === 'builtin' && selectdTools[1]?.children && selectdTools[1].children.length > 1)) {
        operation = selectdTools[2].value
      } else if (selectdTools[0].value === 'custom') {
        operation = selectdTools[2].method_id
      }

      const tool = {
        ...selectdTools[2],
        label: selectdTools[0].value === 'custom' ? selectdTools[2].label : selectdTools[2].description,
        tool_id: selectdTools[1].value as string,
        enabled: true
      }
      if (operation) {
        tool.operation = operation
      }
      refresh(tool)
      handleClose()
    })
  }
  /** Load cascader options dynamically */
  const loadData = (selectedOptions: ToolOption[]) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    if (selectedOptions.length === 1) {
      getTools({ tool_type: targetOption.value as ToolType })
        .then(res => {
          const response = res as ToolItem[]
          targetOption.children = response.map((vo: any) => {
            return {
              value: vo.id,
              label: vo.name,
              isLeaf: response.length === 0,
            }
          })
          setOptionList([...optionList])
        })
    } else {
      getToolMethods(targetOption.value as string)
        .then(res => {
          const response = res as Array<{ method_id: string; name: string }>
          targetOption.children = response.map((vo: any) => {
            return {
              value: vo.name,
              label: vo.name,
              description: vo.description,
              isLeaf: true,
              method_id: vo.method_id,
              parameters: vo.parameters
            }
          })
          setOptionList([...optionList])
        })
    }
  };

  /** Handle cascader selection change */
  const handleChange: CascaderProps<ToolOption>['onChange'] = (_value, selectedOptions) => {
    console.log('selectedOptions', selectedOptions)
    setSelectedTools(selectedOptions)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbModal
      title={t(`application.addTool`)}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="agent_id"
          label={t('application.tool')}
          rules={[
            { required: true, message: t('common.pleaseSelect') },
          ]}
        >
          <Cascader
            placeholder={t('common.pleaseSelect')}
            options={optionList}
            loadData={loadData}
            onChange={handleChange}
            changeOnSelect={false}
          />
        </FormItem>
      </Form>
    </RbModal>
  );
});

export default ToolModal;