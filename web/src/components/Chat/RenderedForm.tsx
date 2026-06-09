import { useEffect, useRef, useState } from 'react'
import { Divider, Button, type ButtonProps } from 'antd'
import { useTranslation } from 'react-i18next'

import Markdown from '@/components/Markdown'
import { formatDateTime } from '@/utils/format'

interface FormField {
  id: string
  default_value?: string
}

interface Action {
  id: string
  label?: string
  variant?: ButtonProps['type']
  status?: string
}

interface RenderedFormProps {
  content: string
  formFields?: FormField[]
  actions?: Action[];
  variables?: Record<string, any>;
  onActionClick?: (actionId: string, fieldValues: Record<string, string>) => void
  editable?: boolean;
  resolved_action_id?: string;
  timeout_at?: number;
}

// 渲染内容方法：处理 content、form_fields、actions 并替换变量
export const renderContent = (
  content: string,
  formFields: FormField[] = [],
  variables?: Record<string, any>,
  editable: boolean = true,
): string => {

  let renderedContent = content
  
  // 替换 {{xx.xx}} 变量为对应的值
  renderedContent = renderedContent.replace(/\{\{([^}]+)\}\}/g, (match: string, varName: string) => {
    const trimmedVarName = varName.trim()
    
    // 检查是否是表单字段
    if (trimmedVarName.startsWith('form_field:')) {
      const fieldId = trimmedVarName.replace('form_field:', '')
      const field = formFields.find((f) => f.id === fieldId)
      
      if (editable && field) {
        // 可编辑状态：渲染表单元素
        return `<textarea id="${fieldId}" name="${fieldId}" default_value="${field.default_value || ''}" rows="4" class="rb:w-full rb:border rb:border-gray-200 rb:rounded-lg rb:p-2 rb:text-sm"></textarea>`
      } else if (field) {
        // 不可编辑状态：替换为 default_value
        return field.default_value || ''
      }
      return match
    }
    
    // 尝试从 variables 中获取对应的值
    
    if (trimmedVarName.split('.').length === 3) {
      const [namespace, type, ...keys] = trimmedVarName.split('.')
      return variables?.[namespace]?.[type]?.[keys.join('.')] || match
    }
    return variables?.[trimmedVarName] || match
  })
  
  return renderedContent
}

const RenderedForm: React.FC<RenderedFormProps> = ({ 
  content, 
  formFields = [], 
  actions = [], 
  onActionClick,
  variables = {},
  editable = true,
  resolved_action_id,
  timeout_at
}) => {
  const { t } = useTranslation()
  const [renderedContent, setRenderedContent] = useState<string | undefined>(undefined)
  const formRef = useRef<HTMLFormElement>(null)

  useEffect(() => {
    const newRenderedContent = renderContent(content, formFields, variables, editable)
    setRenderedContent(newRenderedContent)
  }, [content, formFields, actions, variables, editable])

  const handleButtonClick = (actionId: string) => {
    console.log('form', formRef.current)
    if (formRef.current && onActionClick) {
      const formData = new FormData(formRef.current)
      const fieldValues: Record<string, string> = {}
      
      formData.forEach((value, key) => {
        fieldValues[key] = String(value)
      })
      
      onActionClick(actionId, fieldValues)
    }
  }

  if (!renderedContent) {
    return null
  }
  
  // 不可编辑状态使用纯文本展示，可编辑状态使用 Markdown 渲染
  if (!editable) {
    return <div className="rb:text-gray-600 rb:text-sm">
      <Markdown content={renderedContent} />
      {resolved_action_id && <>
        <Divider />
        {t('memoryConversation.triggeredAction')}: {resolved_action_id || ''}
      </>}
    </div>
  }
  
  return <>
    <form ref={formRef}>
      <Markdown content={renderedContent} />
      {editable && actions.length > 0 && (
        <div className="rb:mt-2">
          {actions.map((action, index: number) => (
            <Button
              key={action.id || index}
              type={action.variant || 'primary'}
              onClick={() => handleButtonClick(action.id)}
            >
              {action.label || `Action ${index + 1}`}
            </Button>
          ))}
        </div>
      )}
    </form>
    {!resolved_action_id && timeout_at && <>
      <Divider />
      {t('memoryConversation.timeout_at', { timeout_at: formatDateTime(timeout_at) })}
    </>}
  </>
}

export default RenderedForm
