/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:06 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 10:52:13
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
 * Component Props
 */
interface ToolModalProps {
  /** Callback to add selected tool to parent component */
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
  
  /**
   * Initial cascader options for tool types
   * Level 1: Tool type selection (MCP, Builtin, Custom)
   */
  const [optionList, setOptionList] = useState<ToolOption[]>([
    { value: 'mcp', label: t('tool.mcp'), isLeaf: false },
    { value: 'builtin', label: t('tool.inner'), isLeaf: false },
    { value: 'custom', label: t('tool.custom'), isLeaf: false },
  ])
  
  /**
   * Stores the complete selection path
   * [0] = Tool type, [1] = Specific tool, [2] = Tool method
   */
  const [selectdTools, setSelectedTools] = useState<ToolOption[]>([])

  /**
   * Closes the modal and resets all state
   * Clears form, loading state, and selections
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setSelectedTools([])
  };

  /**
   * Opens the modal and resets state
   * Clears any previous selections
   */
  const handleOpen = () => {
    setVisible(true);
    form.resetFields();
    setSelectedTools([])
  };
  
  /**
   * Saves the selected tool and closes modal
   */
  const handleSave = () => {
    form.validateFields().then(() => {
      setLoading(false)
      let operation: any = undefined

      // Determine operation based on tool type
      if (selectdTools[0].value === 'mcp' || 
          (selectdTools[0].value === 'builtin' && 
           selectdTools[1]?.children && 
           selectdTools[1].children.length > 1)) {
        // MCP or builtin with multiple methods: use method name
        operation = selectdTools[2].value
      } else if (selectdTools[0].value === 'custom') {
        // Custom tools: use method_id
        operation = selectdTools[2].method_id
      }

      // Construct tool object
      const tool = {
        ...selectdTools[2],
        // Custom tools use label, others use description
        label: selectdTools[0].value === 'custom' ? selectdTools[2].label : selectdTools[2].description,
        tool_id: selectdTools[1].value as string,
        enabled: true
      }
      
      // Add operation if determined
      if (operation) {
        tool.operation = operation
      }
      
      refresh(tool)
      handleClose()
    })
  }
  
  /**
   * Dynamically loads cascader options based on selection
   */
  const loadData = (selectedOptions: ToolOption[]) => {
    const targetOption = selectedOptions[selectedOptions.length - 1];
    
    if (selectedOptions.length === 1) {
      // Level 1 selected: Load tools of this type
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
      // Level 2 selected: Load methods for this tool
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

  /**
   * Handles cascader selection change
   */
  const handleChange: CascaderProps<ToolOption>['onChange'] = (_value, selectedOptions) => {
    console.log('selectedOptions', selectedOptions)
    setSelectedTools(selectedOptions)
  }

  /**
   * Exposes methods to parent component via ref
   */
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
          {/* Three-level cascading selector */}
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
