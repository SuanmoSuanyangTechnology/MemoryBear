import { type FC, useState, useRef, useEffect } from 'react';
import { Button, Form, Input, App, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'
import copy from 'copy-to-clipboard';

import { updatePromptMessages, createPromptSessions } from '@/api/prompt'
import { getModelListUrl } from '@/api/models'
import type { PromptVariableModalRef, AiPromptForm, HistoryItem, PromptSaveModalRef } from './types'
import ChatContent from '@/components/Chat/ChatContent'
import Empty from '@/components/Empty'
import ChatSendIcon from '@/assets/images/application/chatSend.svg'
import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import type { ChatItem } from '@/components/Chat/types' 
import CustomSelect from '@/components/CustomSelect'
import PromptVariableModal from './components/PromptVariableModal'
import { type SSEMessage } from '@/utils/stream'
import Editor from '@/views/ApplicationConfig/components/Editor'
import PromptSaveModal from './components/PromptSaveModal'

const Prompt: FC<{ editVo: HistoryItem | null; refresh: () => void; }> = ({ editVo, refresh }) => {
  const { t } = useTranslation();
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

  useEffect(() => {
    if (editVo?.id) {
      form.setFieldValue('current_prompt', editVo.prompt)
      setChatList([])
    }
    updateSession()
  }, [editVo])

  const updateSession = () => {
    console.log('updateSession')
    createPromptSessions().then(res => {
      const response = res as { id: string }
      setPromptSession(response.id)
    })
  }

  const handleSend = () => {
    if (!promptSession) return
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
            // 流结束时同步表单值
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
  const handleCopy = () => {
    if (!values.current_prompt || values?.current_prompt?.trim() === '') return
    copy(values.current_prompt)
    message.success(t('common.copySuccess'))
  }
  const handleAdd = () => {
    aiPromptVariableModalRef.current?.handleOpen()
  }
  const handleVariableApply = (value: string) => {
    if (editorRef.current?.insertText) {
      editorRef.current.insertText(value)
    } else {
      form.setFieldValue('current_prompt', (values.current_prompt || '') + value)
    }
  }
  const handleSave = () => {
    if (!values.current_prompt || !promptSession) {
      return
    }
    promptSaveModalRef.current?.handleOpen({
      session_id: promptSession,
      prompt: values.current_prompt
    })
  }

  const handleRefresh = () => {
    form.setFieldValue('current_prompt', undefined)
    currentPromptValueRef.current = undefined;
    setChatList([])
    refresh()
  }

  console.log(values)
  return (
    <>
      <Form form={form}>
        <div className="rb:grid rb:grid-cols-2 rb:-my-4">
          <div className="rb:border-r rb:border-r-[#EBEBEB] rb:pr-6 rb:pt-3">
            <Form.Item
              label={t('prompt.model')}
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
              classNames="rb:h-[calc(100vh-260px)] rb:px-[16px] rb:py-[20px] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-[8px]"
              contentClassNames="rb:max-w-[260px]!"
              empty={<Empty url={ConversationEmptyIcon} title={t('prompt.promptChatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
              data={chatList || []}
              streamLoading={false}
              labelPosition="top"
              labelFormat={(item) => item.role === 'user' ? t('prompt.you') : t('prompt.ai')}
            />

            <div className="rb:flex rb:items-center rb:gap-2.5 rb:py-4">
              <Form.Item name="message" className="rb:mb-0!" style={{ width: 'calc(100% - 54px)' }}>
                <Input
                  className="rb:h-11 rb:shadow-[0px_2px_8px_0px_rgba(33,35,50,0.1)]"
                  placeholder={t('prompt.promptChatPlaceholder')}
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
                <Form.Item label={t('prompt.conversationOptimizationPrompt')}></Form.Item>
              </Col>
              <Col span={12} className="rb:text-right">
                <Button onClick={handleAdd}>+ {t('prompt.addVariable')}</Button>
              </Col>
            </Row>
            <Form.Item name="current_prompt">
              <Editor 
                ref={editorRef}
                placeholder={t('prompt.promptPlaceholder')}
                className="rb:h-[calc(100vh-260px)]"
                // onChange={(value) => form.setFieldValue('current_prompt', value)}
              />
            </Form.Item>
            <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-6">
              <Button type="primary" block disabled={!values?.current_prompt} onClick={handleSave}>{t('common.save')}</Button>
              <Button block disabled={!values?.current_prompt} onClick={handleCopy}>{t('common.copy')}</Button>
            </div>
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