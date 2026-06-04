import { useEffect, useState } from 'react'
import { App, Divider } from 'antd'
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
  variant?: string
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
  actions: Action[] = [],
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
    
    return variables?.[trimmedVarName] || match
  })
  
  // 在结尾拼接 actions 按钮（使用 form 包裹）
  if (editable && actions.length > 0) {
    const actionButtons = actions.map((action, index: number) => {
      return `<button data-variant="${action.variant}" data-action-id="${action.id}" class="rb:mt-2 rb:px-4 rb:py-2 rb:bg-blue-500 rb:text-white rb:rounded-lg rb:text-sm rb:cursor-pointer rb:hover:bg-blue-600">${action.label || `Action ${index + 1}`}</button>`
    }).join('&nbsp;&nbsp;')
    renderedContent = `<form id="rendered-form" class="rb:space-y-4">${renderedContent}\n\n<div class="rb:mt-2">${actionButtons}</div></form>`
  } else if (editable) {
    renderedContent = `<form id="rendered-form" class="rb:space-y-4">${renderedContent}</form>`
  }
  
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
  const { message } = App.useApp()
  const [renderedContent, setRenderedContent] = useState<string | undefined>(undefined)

  useEffect(() => {
    const newRenderedContent = renderContent(content, formFields, actions, variables, editable)
    setRenderedContent(newRenderedContent)
  }, [content, formFields, actions, variables, editable])

  // 可编辑状态下添加表单提交事件监听
  useEffect(() => {
    if (!editable) {
      return
    }
    
    let clickedActionId: string | null = null
    
    // 监听按钮点击事件
    const handleButtonClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const button = target.closest('button[data-action-id]')
      if (button) {
        clickedActionId = button.getAttribute('data-action-id')
        // 提交表单
        const form = document.getElementById('rendered-form') as HTMLFormElement
        if (form) {
          form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
        }
      }
    }
    
    // 处理表单提交
    const handleFormSubmit = (e: Event) => {
      e.preventDefault()
      const target = e.target as HTMLFormElement
      const formData = new FormData(target)
      const actionId = clickedActionId || formData.get('action') || null
      const fieldValues: Record<string, string> = {}
      
      // 获取所有 form_field 的值
      formData.forEach((value, key) => {
        fieldValues[key] = String(value)
      })
      
      console.log('Form submitted with action id:', actionId)
      console.log('Form field values:', fieldValues)
      
      // 调用回调函数
      if (onActionClick && actionId) {
        onActionClick(String(actionId), fieldValues)
      }
      
      // 重置 clickedActionId
      clickedActionId = null
    }
    let form: HTMLFormElement | null = null;
    setTimeout(() => {
      // 添加事件监听
      document.addEventListener('click', handleButtonClick)
      form = document.getElementById('rendered-form') as HTMLFormElement
      console.log('Add event listeners', form)
      if (form) {
        form.addEventListener('submit', handleFormSubmit)
      }
    }, 0)
    
    return () => {
      document.removeEventListener('click', handleButtonClick)
      if (form) {
        form.removeEventListener('submit', handleFormSubmit)
      }
    }
  }, [content, formFields, actions, onActionClick, message, editable])

  if (!renderedContent) {
    return null
  }
  
  // 不可编辑状态使用纯文本展示，可编辑状态使用 Markdown 渲染
  if (!editable) {
    return <div className="rb:text-gray-600 rb:text-sm">
      <Markdown content={renderedContent} />
      <Divider />
      {t('memoryConversation.triggeredAction')}: {resolved_action_id || ''}
    </div>
  }
  
  return <>
    <Markdown content={renderedContent} />
    {!resolved_action_id && timeout_at && <>
      <Divider />
      {t('memoryConversation.timeout_at', { timeout_at: formatDateTime(timeout_at) })}
    </>}
  </>
}

export default RenderedForm
