/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:26:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:26:44 
 */
/**
 * AI Prompt Assistant Modal
 * Provides an interactive chat interface to help users optimize their prompts using AI
 * Features model selection, chat history, and variable insertion
 */

import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Button, Form, Input, App, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import copy from 'copy-to-clipboard';

import { updatePromptMessages, createPromptSessions } from '@/api/prompt'
import { getModelListUrl } from '@/api/models'
import type { AiPromptModalRef, AiPromptVariableModalRef, AiPromptForm } from '../types'
import RbModal from '@/components/RbModal'
import type { ModelListItem } from '@/views/ModelManagement/types'
import ChatContent from '@/components/Chat/ChatContent'
import Empty from '@/components/Empty'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import type { ChatItem } from '@/components/Chat/types' 
import CustomSelect from '@/components/CustomSelect'
import AiPromptVariableModal from './AiPromptVariableModal'
import { type SSEMessage } from '@/utils/stream'
import Editor from './Editor'

/**
 * Component props
 */
interface AiPromptModalProps {
  /** Callback to refresh prompt with optimized value */
  refresh: (value: string) => void;
  /** Default model to pre-select */
  defaultModel: ModelListItem | null;
}

/**
 * AI Prompt Assistant Modal Component
 * Helps users create and optimize prompts through AI-powered conversation
 */
const AiPromptModal = forwardRef<AiPromptModalRef, AiPromptModalProps>(({
  refresh,
  defaultModel,
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
      message.warning(t('common.selectPlaceholder', { title: t('application.model') }))
      return
    }
    if (!values.message) {
      message.warning(t('application.promptChatPlaceholder'))
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
    updatePromptMessages(promptSession, values, handleStreamMessage)
      // .then(res => {
      //   const response = res as { prompt: string; desc: string; variables: string[] }
      //   form.setFieldsValue({ current_prompt: response.prompt })
      //   setChatList(prev => {
      //     return [...prev, { role: 'assistant', content: response.desc }]
      //   })
      //   setVariables(response.variables)
      // })
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

  console.log(values)
  return (
    <RbModal
      title={t('application.AIPromptAssistant')}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
    >
      <Form form={form}>
        <div className="rb:grid rb:grid-cols-2 rb:border-t rb:border-t-[#EBEBEB]">
          <div className="rb:border-r rb:border-r-[#EBEBEB] rb:pr-6 rb:pt-3">
            <Form.Item
              label={t('application.model')}
              name="model_id"
              rules={[{ required: true, message: t('common.pleaseSelect') }]}
            >
              <CustomSelect
                url={getModelListUrl}
                params={{ type: 'llm,chat', pagesize: 100, is_active: true }}
                valueKey="id"
                labelKey="name"
                hasAll={false}
                style={{ width: '100%' }}
              />
            </Form.Item>

            <ChatContent
              classNames="rb:h-100.5 rb:px-[16px] rb:py-[20px] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-[8px]"
              contentClassNames="rb:max-w-[260px]!"
              empty={<Empty url={ConversationEmptyIcon} title={t('application.promptChatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
              data={chatList || []}
              streamLoading={false}
              labelPosition="top"
              labelFormat={(item) => item.role === 'user' ? t('application.you') : t('application.ai')}
            />

            <div className="rb:flex rb:items-center rb:gap-2.5 rb:py-4">
              <Form.Item name="message" className="rb:mb-0!" style={{ width: 'calc(100% - 54px)' }}>
                <Input
                  className="rb:h-11 rb:shadow-[0px_2px_8px_0px_rgba(33,35,50,0.1)]"
                  placeholder={t('application.promptChatPlaceholder')}
                  onPressEnter={handleSend}
                />
              </Form.Item>
              <img src={ChatSendIcon} className={clsx("rb:w-11 rb:h-11 rb:cursor-pointer", {
                'rb:opacity-50': loading,
              })} onClick={handleSend} />
            </div>
          </div>

          <div className="rb:pl-6 rb:pt-3">
            <Row>
              <Col span={12}>
                <Form.Item label={t('application.conversationOptimizationPrompt')}></Form.Item>
              </Col>
              <Col span={12} className="rb:text-right">
                <Button onClick={handleAdd}>+ {t('application.addVariable')}</Button>
              </Col>
            </Row>
            <Form.Item name="current_prompt">
              <Editor 
                ref={editorRef}
                className="rb:h-100.5 " 
                onChange={(value) => form.setFieldValue('current_prompt', value)}
              />
            </Form.Item>
            <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-6">
              <Button block disabled={!values?.current_prompt} onClick={handleCopy}>{t('common.copy')}</Button>
              <Button type="primary" block disabled={!values?.current_prompt} onClick={handleApply}>{t('application.apply')}</Button>
            </div>
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