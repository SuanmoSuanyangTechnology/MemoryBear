/*
 * @Author: ZhaoYing 
 * @Date: 2025-12-10 16:46:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2025-12-20 15:38:40
 */
import { useEffect } from 'react'
import { Flex, Input, Form } from 'antd'
import SendIcon from '@/assets/images/conversation/send.svg'
import SendDisabledIcon from '@/assets/images/conversation/sendDisabled.svg'
import LoadingIcon from '@/assets/images/conversation/loading.svg'
import type { ChatInputProps } from './types'

/**
 * 聊天输入框组件
 * 提供消息输入、发送功能，支持键盘快捷键和加载状态显示
 */
const ChatInput = ({ message, onChange, onSend, loading, children }: ChatInputProps) => {
  const [form] = Form.useForm()
  // 监听表单值变化，用于控制发送按钮状态
  const values = Form.useWatch([], form);

  // 当外部message为空时，清空表单
  useEffect(() => {
    if (!message) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [form, message])
  
  // 当加载状态时，清空输入框
  useEffect(() => {
    if (loading) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [loading])

  return (
    <div className="rb:absolute rb:bottom-3 rb:left-0 rb:right-0">
      <Flex vertical justify="space-between" className="rb:border rb:border-[#DFE4ED] rb:rounded-xl rb:min-h-30">
        {/* 消息输入表单 */}
        <Form form={form} layout="vertical">
            <Form.Item name="message" noStyle>
              <Input.TextArea
                className="rb:m-[10px_12px_10px_12px]! rb:p-0! rb:w-[calc(100%-24px)]! rb:flex-[1_1_auto]"
                variant="borderless"
                autoSize={{ minRows: 2, maxRows: 2 }}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={(e) => {
                  // Enter键发送，Shift+Enter换行
                  if (e.key === 'Enter' && !e.shiftKey && (e.target as HTMLTextAreaElement).value?.trim() !== '' && !loading) {
                    e.preventDefault();
                    onSend();
                  }
                }}
              />
            </Form.Item>
        </Form>

        {/* 底部操作区域 */}
        <Flex align="center" justify="space-between" className="rb:m-[0_10px_10px_10px]!">
          {/* 子组件内容（如按钮等） */}
          {children}
          {/* 发送按钮 - 根据状态显示不同图标 */}
          {loading
            ? <img src={LoadingIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" />
            : !values || !values?.message || values?.message?.trim() === ''
            ? <img src={SendDisabledIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" />
            : <img src={SendIcon} className="rb:w-5.5 rb:h-5.5 rb:cursor-pointer" onClick={onSend} />
          }
        </Flex>
      </Flex>
    </div>
  )
}

export default ChatInput
