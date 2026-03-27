/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:44:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 15:14:58
 */
/**
 * Prompt Editor Component
 * AI-powered prompt optimization with chat interface and variable support
 */

import { type FC, useState, useRef, useEffect } from 'react';
import { Button, Form, Input, App, Flex, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import copy from 'copy-to-clipboard';
import { useNavigate, useLocation } from 'react-router-dom';

import { updatePromptMessages, createPromptSessions } from '@/api/prompt'
import type { PromptVariableModalRef, AiPromptForm, HistoryItem, PromptSaveModalRef } from './types'
import ChatContent from '@/components/Chat/ChatContent'
import Empty from '@/components/Empty'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import type { ChatItem } from '@/components/Chat/types'
import ModelSelect from '@/components/ModelSelect'
import PromptVariableModal from './components/PromptVariableModal'
import { type SSEMessage } from '@/utils/stream'
import Editor from '@/views/ApplicationConfig/components/Editor'
import PromptSaveModal from './components/PromptSaveModal'
import analysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.png'
import Header from './components/Header';
import RbCard from '@/components/RbCard/Card';
import styles from './index.module.css'

const Prompt: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state } = useLocation()
  const { message } = App.useApp()
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm<AiPromptForm>()
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<string[]>([])
  const [promptSession, setPromptSession] = useState<string | null>(null)
  const aiPromptVariableModalRef = useRef<PromptVariableModalRef>(null)
  const promptSaveModalRef = useRef<PromptSaveModalRef>(null)
  const editorRef = useRef<any>(null)
  const currentPromptValueRef = useRef<string>(undefined)
  const values = Form.useWatch([], form)
  const [editVo, setEditVo] = useState<HistoryItem | null>(null)

  useEffect(() => {
    setEditVo(state)
  }, [state])

  useEffect(() => {
    if (editVo?.id) {
      form.setFieldValue('current_prompt', editVo.prompt)
      setChatList([])
    }
    updateSession()
  }, [editVo])

  /** Update session ID */
  const updateSession = () => {
    console.log('updateSession')
    createPromptSessions().then(res => {
      const response = res as { id: string }
      setPromptSession(response.id)
    })
  }

  /** Send message to AI for prompt optimization */
  const handleSend = () => {
    if (!promptSession || loading || !values.message || values.message.trim() == '') return
    if (!values.model_id) {
      message.warning(t('common.selectPlaceholder', { title: t('prompt.model') }))
      return
    }
    if (!values.message) {
      message.warning(t('prompt.promptChatPlaceholder'))
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
            if (typeof content === 'string') {
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
            // Sync form values when stream ends
            form.setFieldsValue({ current_prompt: currentPromptValueRef.current })
            break
        }
      })
    };
    updatePromptMessages((promptSession) as string, values, handleStreamMessage)
      .finally(() => {
        setLoading(false)
      })
  }
  /** Copy prompt to clipboard */
  const handleCopy = () => {
    if (!values.current_prompt || values?.current_prompt?.trim() === '') return
    copy(values.current_prompt)
    message.success(t('common.copySuccess'))
  }
  /** Open variable modal */
  const handleAdd = () => {
    aiPromptVariableModalRef.current?.handleOpen()
  }
  /** Apply variable to editor */
  const handleVariableApply = (value: string) => {
    if (editorRef.current?.insertText) {
      editorRef.current.insertText(value)
    } else {
      form.setFieldValue('current_prompt', (values.current_prompt || '') + value)
    }
  }
  /** Save prompt */
  const handleSave = () => {
    if (!values.current_prompt || !promptSession) {
      return
    }
    promptSaveModalRef.current?.handleOpen({
      session_id: promptSession,
      prompt: values.current_prompt
    })
  }

  /** Refresh editor and clear state */
  const handleRefresh = () => {
    form.setFieldValue('current_prompt', undefined)
    currentPromptValueRef.current = undefined;
    setChatList([])
    setEditVo(null)
    updateSession()
  }
  const [isFocus, setIsFocus] = useState(false)
  const handleFocus = () => {
    setIsFocus(true)
  }
  const handleBlur = () => {
    setIsFocus(false)
  }
  const handleJump = () => {
    navigate('/prompt/history')
  }

  return (
    <>
      <Form form={form}>
        <div className="rb:grid rb:grid-cols-2 rb:gap-3">
          <div>
            <Header title={t(`menu.prompt`)} desc={t('prompt.promptDesc')} className="rb:mb-3" />

            <RbCard
              title={t('prompt.chatTitle')}
              headerClassName="rb:min-h-[52px]! rb:font-[MiSans-Bold] rb:gont-bold"
              headerType="borderless"
              bodyClassName="rb:px-4! rb:pt-0! rb:pb-3!"
            >
              <ChatContent
                classNames="rb:h-[calc(100vh-257px)] rb:mb-[12px]!"
                contentClassNames="rb:max-w-75!"
                empty={<Empty url={ConversationEmptyIcon} title={t(`prompt.promptChatEmpty`)} isNeedSubTitle={false} size={[140, 100]} className="rb:h-full" />}
                data={chatList || []}
                streamLoading={false}
                labelPosition="top"
                labelFormat={(item) => item.role === 'user' ? t(`prompt.you`) : t(`prompt.ai`)}
              />
              <Flex align="center" gap={12} justify="space-between"
                className={clsx("rb-border rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.1)] rb:rounded-2xl rb:h-13 rb:px-3!", {
                  'rb:border rb:border-[#171719]!': isFocus
                })}
              >
                <Form.Item name="message" className="rb:flex-1 rb:mb-0!">
                  <Input
                    placeholder={t(`prompt.promptChatPlaceholder`)}
                    onPressEnter={handleSend}
                    variant="borderless"
                    className="rb:p-0!"
                    onFocus={handleFocus}
                    onBlur={handleBlur}
                  />
                </Form.Item>
                <Flex align="center" justify="center"
                  className={clsx('rb:size-7 rb:rounded-full rb:shadow-[0px 2px 12px 0px rgba(23,23,25,0.1)]', {
                    'rb:cursor-not-allowed rb:bg-[#F6F6F6]': loading || !values || !values?.message || values?.message?.trim() === '',
                    'rb:cursor-pointer rb:bg-[#171719]': !loading && !(!values || !values?.message || values?.message?.trim() === '')
                  })}
                  onClick={handleSend}
                >
                  <div className={clsx("rb:size-4 rb:bg-cover", {
                    "rb:bg-[url('@/assets/images/conversation/loading.svg')]": loading,
                    "rb:bg-[url('@/assets/images/conversation/sendDisabled.svg')]": !loading && (!values || !values?.message || values?.message?.trim() === ''),
                    "rb:bg-[url('@/assets/images/conversation/send.svg')]": !loading && !(!values || !values?.message || values?.message?.trim() === '')
                  })}></div>
                </Flex>
              </Flex>

            </RbCard>
          </div>

          <div>
            <Flex align="center" justify="end" gap={8} className="rb:h-12.5 rb:mb-3!">
              <Form.Item
                name="model_id"
                noStyle
              >
                <ModelSelect
                  params={{ type: 'llm,chat' }}
                  className={`rb:w-75! ${styles.select}`}
                />
              </Form.Item>
              <Button className="rb:border-none!" onClick={handleJump}>{t('prompt.history')}</Button>
            </Flex>
            <RbCard
              title={t('prompt.conversationOptimizationPrompt')}
              headerClassName="rb:min-h-[52px]! rb:font-[MiSans-Bold] rb:gont-bold"
              headerType="borderless"
              bodyClassName="rb:px-4! rb:pt-0! rb:pb-3!"
              extra={
                <Space size={8}>
                  <Button
                    icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></div>}
                    disabled={!values?.current_prompt || loading}
                    onClick={handleSave}
                  >{t('common.save')}</Button>
                  <Button
                    icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/save.svg')]"></div>}
                    disabled={!values?.current_prompt || loading}
                    onClick={handleCopy}
                  >{t('common.copy')}</Button>
                  <Button
                    icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/plus_dark.svg')]"></div>}
                    disabled={!values?.current_prompt || loading}
                    onClick={handleAdd}
                  ></Button>
                </Space>}
            >
              <Form.Item name="current_prompt" noStyle>
                {values?.current_prompt
                  ? <Editor
                    ref={editorRef}
                    className="rb:h-[calc(100vh-193px)] rb:bg-white! rb:border-none! rb:p-0! rb:text-[#212332] rb:leading-5"
                    onChange={(value) => form.setFieldValue('current_prompt', value)}
                  />
                  : <Empty url={analysisEmptyIcon} title={t(`prompt.promptPlaceholder`)} isNeedSubTitle={false} size={[270, 170]} className="rb:h-[calc(100vh-193px)] rb:mx-auto! rb:text-center! rb:text-[12px]! rb:leading-4!" />
                }
              </Form.Item>
            </RbCard>
          </div>
        </div>
      </Form>

      <PromptVariableModal
        ref={aiPromptVariableModalRef}
        variables={variables}
        refresh={handleVariableApply}
      />

      <PromptSaveModal
        ref={promptSaveModalRef}
        refresh={handleRefresh}
      />
    </>
  );
};

export default Prompt;