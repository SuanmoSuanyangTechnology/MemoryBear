/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:44 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-07 17:12:25
 */
/**
 * AI Prompt Assistant Modal
 * Provides an interactive chat interface to help users optimize their prompts using AI
 * Features model selection, chat history, and variable insertion
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Button, Form, Input, App, Flex, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import copy from 'copy-to-clipboard';

import { updatePromptMessages, createPromptSessions } from '@/api/prompt'
import type { AiPromptModalRef, AiPromptVariableModalRef, AiPromptForm } from '../types'
import RbModal from '@/components/RbModal'
import type { ModelListItem } from '@/views/ModelManagement/types'
import ChatContent from '@/components/Chat/ChatContent'
import Empty from '@/components/Empty'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import type { ChatItem } from '@/components/Chat/types'
import ModelSelect from '@/components/ModelSelect'
import AiPromptVariableModal from './AiPromptVariableModal'
import { type SSEMessage } from '@/utils/stream'
import Editor from './Editor'
import analysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.png'

/**
 * Component props
 */
interface AiPromptModalProps {
  /** Callback to refresh prompt with optimized value */
  refresh: (value: string) => void;
  /** Default model to pre-select */
  defaultModel?: ModelListItem | null;
  source?: 'application' | 'skills'
}

/**
 * AI Prompt Assistant Modal Component
 * Helps users create and optimize prompts through AI-powered conversation
 */
const AiPromptModal = forwardRef<AiPromptModalRef, AiPromptModalProps>(({
  refresh,
  defaultModel,
  source = 'application'
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm<AiPromptForm>()
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<string[]>([])
  const [promptSession, setPromptSession] = useState<string | null>(null)
  const aiPromptVariableModalRef = useRef<AiPromptVariableModalRef>(null)
  const editorRef = useRef<any>(null)
  const currentPromptValueRef = useRef<string>('')

  const values = Form.useWatch([], form)

  /** Close modal and reset state */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
    setChatList([])
    setVariables([])
    form.setFieldsValue({
      message: undefined,
      current_prompt: undefined,
    })
  };

  /** Open modal and create new prompt session */
  const handleOpen = () => {
    createPromptSessions()
      .then(res => {
        const response = res as { id: string }
        setPromptSession(response.id)

        if (!values.model_id && defaultModel?.id) {
          form.setFieldValue('model_id', defaultModel?.id)
        }
        setVisible(true);
      })
  };
  /** Send user message and get AI response */
  const handleSend = () => {
    if (!promptSession) return
    if (!values.model_id) {
      message.warning(t('common.selectPlaceholder', { title: t(`${source}.model`) }))
      return
    }
    if (!values.message) {
      message.warning(t(`${source}.promptChatPlaceholder`))
      return
    }
    const messageContent = values.message
    setLoading(true)
    setChatList(prev => {
      return [...prev, { role: 'user', content: messageContent}]
    })
    form.setFieldsValue({ message: undefined, current_prompt: undefined })

    const handleStreamMessage = (data: SSEMessage[]) => {
      data.map(item => {
        const { content, desc, variables } = item.data as { content: string; desc: string; variables: string[] };

        switch (item.event) {
          case 'start':
            currentPromptValueRef.current = ''
            if (editorRef.current?.clear) {
              editorRef.current.clear();
            }
            break;
          case 'message':
            if (content) {
              currentPromptValueRef.current += content;
              if (editorRef.current?.appendText) {
                editorRef.current.appendText(content);
                editorRef.current.scrollToBottom();
              } else {
                form.setFieldsValue({ current_prompt: currentPromptValueRef.current })
              }
            }
            if (desc) {
              setChatList(prev => {
                return [...prev, { role: 'assistant', content: desc }]
              })
            }
            if (variables) {
              setVariables(variables)
            }
            break;
          case 'end':
            setLoading(false)
            // Sync form value when stream ends
            form.setFieldsValue({ current_prompt: currentPromptValueRef.current })
            break
        }
      })
    };
    updatePromptMessages(promptSession, {
      ...values,
      skill: source === 'skills'
    }, handleStreamMessage)
      .finally(() => {
        setLoading(false)
      })
  }
  /** Copy current prompt to clipboard */
  const handleCopy = () => {
    if (!values.current_prompt || values?.current_prompt?.trim() === '') return
    copy(values.current_prompt)
    message.success(t('common.copySuccess'))
  }
  /** Open variable selection modal */
  const handleAdd = () => {
    aiPromptVariableModalRef.current?.handleOpen()
  }
  /** Insert variable into prompt editor */
  const handleVariableApply = (value: string) => {
    if (editorRef.current?.insertText) {
      editorRef.current.insertText(value)
    } else {
      form.setFieldValue('current_prompt', (values.current_prompt || '') + value)
    }
  }
  /** Apply optimized prompt and close modal */
  const handleApply = () => {
    if (!values.current_prompt) {
      return
    }
    refresh(values.current_prompt)
    handleClose()
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));
  const [isFocus, setIsFocus] = useState(false)
  const handleFocus = () => {
    setIsFocus(true)
  }
  const handleBlur = () => {
    setIsFocus(false)
  }

  console.log(values)
  return (
    <RbModal
      title={t(`${source}.AIPromptAssistant`)}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
      classNames={{
        content: 'rb:p-0!',
        header: 'rb:p-6! rb:mb-0!',
        body: 'rb:p-0! rb:border-t rb:border-t-[#EBEBEB]'
      }}
    >
      <Form form={form} className="rb:mx-4!">
        <div className="rb:grid rb:grid-cols-2">
          <div className="rb:border-r rb:border-r-[#EBEBEB] rb:pr-4 rb:pt-3 rb:pb-4">
            <Form.Item
              name="model_id"
              rules={[{ required: true, message: t('common.pleaseSelect') }]}
            >
              <ModelSelect
                params={{ type: 'llm,chat' }}
                className="rb:w-full!"
              />
            </Form.Item>

            <ChatContent
              classNames="rb:h-105.5 rb:pb-[15px]!"
              contentClassNames="rb:max-w-75!"
              empty={<Empty url={ConversationEmptyIcon} title={t(`${source}.promptChatEmpty`)} isNeedSubTitle={false} size={[140, 100]} className="rb:h-full" />}
              data={chatList || []}
              streamLoading={false}
              labelPosition="top"
              labelFormat={(item) => item.role === 'user' ? t(`${source}.you`) : t(`${source}.ai`)}
            />
            <Flex align="center" gap={12} justify="space-between"
              className={clsx("rb-border rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.1)] rb:rounded-2xl rb:h-13 rb:px-3!", {
                'rb:border rb:border-[#171719]!': isFocus
              })}
            >
              <Form.Item name="message" className="rb:flex-1 rb:mb-0!">
                <Input
                  placeholder={t(`${source}.promptChatPlaceholder`)}
                  onPressEnter={handleSend}
                  variant="borderless"
                  className="rb:p-0!"
                  onFocus={handleFocus}
                  onBlur={handleBlur}
                />
              </Form.Item>
              {loading
                ? <div className="rb:size-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/loading.svg')]"></div>
                : !values || !values?.message || values?.message?.trim() === ''
                  ? <div className="rb:size-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/sendDisabled.svg')]"></div>
                  : <div className="rb:size-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/conversation/send.svg')]" onClick={handleSend}></div>
              }
            </Flex>
          </div>

          <div className="rb:pl-4 rb:pt-3.5 rb:pb-4">
            <Flex justify="space-between" className="rb:mb-3!">
              <div>
                {t(`${source}.conversationOptimizationPrompt`)}
              </div>
              <Space size={8}>
                <Button
                  disabled={!values?.current_prompt}
                  icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/application/copy.svg')]"></div>}
                  onClick={handleCopy}>{t('common.copy')}</Button>
                <Button
                  disabled={!values?.current_prompt}
                  icon={<div className="rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/application/save.svg')]"></div>}
                  onClick={handleApply}
                >{t(`${source}.apply`)}</Button>
                {source === 'application' &&
                  <Button
                    disabled={!values?.current_prompt}
                    icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/plus_dark.svg')]"></div>}
                    onClick={handleAdd}
                  ></Button>
                }
              </Space>
            </Flex>

            <Form.Item name="current_prompt" noStyle>
              {values?.current_prompt
                ? <Editor 
                  ref={editorRef}
                  className="rb:h-119 rb:bg-white! rb:border-none! rb:p-0!" 
                  onChange={(value) => form.setFieldValue('current_prompt', value)}
                />
                : <Empty url={analysisEmptyIcon} title={t(`${source}.promptOptimizationEmpty`)} isNeedSubTitle={false} size={[270, 170]} className="rb:h-119 rb:w-70 rb:mx-auto! rb:text-center! rb:text-[12px]! rb:leading-4!" />
              }
            </Form.Item>
          </div>
        </div>
      </Form>

      <AiPromptVariableModal
        ref={aiPromptVariableModalRef}
        variables={variables}
        refresh={handleVariableApply}
      />
    </RbModal>
  );
});

export default AiPromptModal;