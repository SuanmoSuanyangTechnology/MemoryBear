/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:09:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 15:00:15
 */
/**
 * Memory Conversation Page
 * Interactive conversation interface with memory analysis
 * Supports deep thinking, normal reply, and quick reply modes
 */

import { type FC, type ReactNode, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Col, Row, App, Skeleton, Select, Segmented, Tooltip, Flex } from 'antd'
import dayjs from 'dayjs'
import type { AnyObject } from 'antd/es/_util/type';

import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.png'
import { readService, getUserMemoryList } from '@/api/memory'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import type { Data } from '@/views/UserMemory/types'
import Chat from '@/components/Chat'
import type { ChatItem } from '@/components/Chat/types'
import RbCard from '@/components/RbCard/Card';
import styles from './index.module.css'
import ResultCard from '@/components/RbCard/ResultCard'


/** Search mode configuration */
const searchSwitchList = [
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/deepThinking.svg')]"></div>,
    value: '0',
    key: 'deepThinking'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/normalReply.svg')]"></div>,
    value: '1',
    key: 'normalReply'
  },
  {
    icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/quickReply.svg')]"></div>,
    value: '2',
    key: 'quickReply'
  },
]

/**
 * Test parameters for conversation API
 */
export interface TestParams {
  /** End user identifier */
  end_user_id: string;
  /** User message content */
  message: string;
  /** Search mode switch (0: deep thinking, 1: normal, 2: quick) */
  search_switch: string;
  /** Conversation history */
  history: { role: string; content: string }[];
  /** Enable web search */
  web_search?: boolean;
  /** Enable memory function */
  memory?: boolean;
  /** Conversation ID */
  conversation_id?: string;
}
/**
 * Data item in analysis logs
 */
interface DataItem {
    id: string;
    question: string;
    type: string;
    reason: string;
  }
/**
 * Log item for conversation analysis
 */
export interface LogItem {
  type: string;
  title: string;
  data?: DataItem[] | AnyObject;
  raw_results?: string | Record<string, AnyObject>;
  summary?: string;
  query?: string;
  reason?: string;
  result?: string;
  original_query: string;
  index?: number;
}

/**
 * Content wrapper component for analysis items
 */
const ContentWrapper: FC<{ children: ReactNode }> = ({ children }) => (
  <div className="rb:px-3 rb:py-2.5 rb:bg-white rb:rounded-xl">
    {children}
  </div>
)

const MemoryConversation: FC = () => {
  const { t } = useTranslation()
  const { message } = App.useApp();
  const [userId, setUserId] = useState<string>()
  const [loading, setLoading] = useState<boolean>(false)
  const [chatData, setChatData] = useState<ChatItem[]>([])
  const [logs, setLogs] = useState<LogItem[]>([])
  const [userList, setUserList] = useState<Data[]>([])
  const [search_switch, setSearchSwitch] = useState('0')
  const [msg, setMsg] = useState<string>('')
  const [expandedLogs, setExpandedLogs] = useState<Record<number, boolean>>({})

  /** Load user list on mount */
  useEffect(() => {
    getUserMemoryList().then(res => {
      setUserList((res as Data[] || []).map(item => ({
        ...item,
        name: item.end_user?.other_name && item.end_user?.other_name !== '' ? item.end_user?.other_name : item.end_user?.id
      })))
    })
  }, [])

  /** Handle message send */
  const handleSend = () => {
    if(!userId) {
      message.warning(t('common.inputPlaceholder', { title: t('memoryConversation.userID') }))
      return
    }
    setChatData(prev => [...prev, { content: msg, created_at: new Date().getTime(), role: 'user' }])
    setLoading(true)
    setExpandedLogs({})
    readService({
      message: msg,
      end_user_id: userId,
      search_switch: search_switch,
      history: [],
    })
      .then(res => {
        const response = res as { answer: string; intermediate_outputs: LogItem[] }
        setChatData(prev => [...prev, { content: response.answer || '-', created_at: new Date().getTime(), role: 'assistant' }])
        setLogs(response.intermediate_outputs)
        setExpandedLogs(Object.fromEntries(response.intermediate_outputs.map((_, i) => [i, true])))
      })
      .finally(() => {
        setLoading(false)
      })
  }

  /** Handle search mode change */
  const handleChange = (value: string) => {
    setSearchSwitch(value)
  }

  return (
    <>
      <Row gutter={16}>
        <Col span={12}>
          <Select
            options={userList.map(item => ({
              value: item.end_user?.id,
              label: item?.name,
            }))}
            filterOption={(inputValue, option) => option?.label?.toLowerCase().indexOf(inputValue.toLowerCase()) !== -1}
            showSearch={true}
            // filterOption={(inputValue, option) => option.label?.toLowerCase().indexOf(inputValue.toLowerCase()) !== -1}
            placeholder={t('memoryConversation.searchPlaceholder')}
            style={{ width: '100%', marginBottom: '16px' }}
            onChange={setUserId}
            variant="borderless"
            className="rb:bg-white rb:rounded-lg"
          />
        </Col>
      </Row>
      <Row gutter={16} className="rb:h-[calc(100vh-118px)]!">
        <Col span={12} className="rb:h-full!">
          <RbCard 
            title={t('memoryConversation.conversationContent')}
            headerType="borderless"
            headerClassName="rb:min-h-[52px]! rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:px-3! rb:py-0! rb:h-[calc(100%-52px)]!"
            className="rb:h-full!"
          >
            <Chat
              empty={
                <Empty url={ConversationEmptyIcon} className="rb:h-full" size={[140, 100]} title={t('memoryConversation.conversationContentEmpty')} isNeedSubTitle={false} />
              }
              className="rb:pt-0!"
              contentClassName='rb:h-[calc(100%-144px)]'
              data={chatData}
              onChange={setMsg}
              onSend={handleSend}
              loading={loading}
              labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
            >
              <Segmented
                options={searchSwitchList.map(item => ({
                  ...item,
                  icon: <Tooltip title={t(`memoryConversation.${item.key}`)}>{item.icon}</Tooltip>
                }))}
                shape="round"
                className={styles.segmented}
                onChange={handleChange}
              />
            </Chat>
          </RbCard>
        </Col>
        <Col span={12} className="rb:h-full!">
          <RbCard 
            title={t('memoryConversation.memoryConversationAnalysis')}
            headerType="borderless"
            headerClassName="rb:min-h-[52px]! rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-52px)]! rb:overflow-y-auto!"
            className="rb:h-full!"
          >
            {loading ?
              <Skeleton active />
            : !logs || logs.length === 0 ?
              <Empty 
                url={AnalysisEmptyIcon}
                className="rb:h-full"
                title={t('memoryConversation.memoryConversationAnalysisEmpty')}
                subTitle={t('memoryConversation.memoryConversationAnalysisEmptySubTitle')}
                size={[270, 170]}
              />
              : <Flex gap={12} vertical>
                {logs.map((log, logIndex) => (
                  <ResultCard
                    key={logIndex}
                    title={log.title}
                    isMiSans={false}
                    bodyClassName={`rb:p-3! rb:pt-0! ${!!expandedLogs[logIndex] ? 'rb:pb-3!' : 'rb:pb-0!'}`}
                    expanded={!!expandedLogs[logIndex]}
                    handleExpand={() => setExpandedLogs(prev => ({ ...prev, [logIndex]: !prev[logIndex] }))}
                    extra={log.type === 'verification' && <div className="rb-border rb:rounded-lg rb:py-1 rb:px-2 rb:text-[12px] rb:font-medium rb:leading-4.5 rb:text-[#FF5D34]">{log.result}</div>}
                  >
                    {log.type === 'problem_split' && Array.isArray(log.data) && log.data.length > 0 
                      ? <Flex gap={12} vertical>
                        {log.data.map(vo => (
                          <ContentWrapper key={vo.id}>
                            <>
                              <div className="rb:font-medium rb:text-[#212332]">{vo.id}. {vo.question}</div>
                              <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">{vo.reason}</div>
                            </>
                          </ContentWrapper>
                        ))}
                      </Flex>
                      : log.type === 'problem_extension' && log.data && Object.keys(log.data).length > 0 
                      ? <Flex gap={12} vertical>
                        {Object.keys(log.data).map((key: string) => (
                          <ContentWrapper key={key}>
                            <>
                              <div className="rb:font-medium rb:text-[#212332]">{key}</div>
                              {(log.data as Record<string, string[]>)[key].map((item, index) => (
                                <div key={index} className="rb:mt-2 rb:text-[#5B6167] rb:text-[12px]">{item}</div>
                              ))}
                            </>
                          </ContentWrapper>
                        ))}
                      </Flex>
                      : log.type === 'search_result' && log.raw_results && typeof log.raw_results !== 'string'
                      ? <ContentWrapper>
                          <div className="rb:font-medium rb:text-[#212332] rb:mb-2">{log.query}</div>
                          {(log.raw_results.reranked_results as AnyObject)?.communities?.length > 0 && <>
                            <div className="rb:font-medium rb:text-[#212332] rb:text-[12px]">{t('memoryConversation.communities')}</div>
                            <ul className='rb:mt-2 rb:text-[12px] rb:text-[#5B6167] rb:list-disc rb:pl-4'>
                                {((log.raw_results.reranked_results as AnyObject)?.communities as { content: string }[]).map((item, index: number) => (
                                <li key={index}>{item.content}</li>
                              ))}
                            </ul>
                          </>}
                          {(log.raw_results.reranked_results as AnyObject)?.summaries?.length > 0 && <>
                            <div className="rb:font-medium rb:text-[#212332] rb:text-[12px]">{t('memoryConversation.summaries')}</div>
                            <ul className='rb:mt-2 rb:text-[12px] rb:text-[#5B6167] rb:list-disc rb:pl-4'>
                              {((log.raw_results.reranked_results as AnyObject)?.summaries as { content: string }[]).map((item, index: number) => (
                                <li key={index}>{item.content}</li>
                              ))}
                            </ul>
                          </>}
                        </ContentWrapper>
                    : log.type === 'retrieval_summary' && log.summary
                    ? <ContentWrapper>
                      <div className="rb:text-[12px] rb:text-[#5B6167]">{log.summary}</div>
                    </ContentWrapper>
                    : log.type === 'verification'
                    ? <ContentWrapper>
                      <div className="rb:font-medium rb:text-[#212332]">{log.query}</div>
                      <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">{log.reason}</div>
                      <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">{log.result}</div>
                    </ContentWrapper>
                    : log.type === 'output_type'
                    ? <ContentWrapper>
                      <div className="rb:font-medium rb:text-[#212332] rb:mb-2">{log.query}</div>
                      <div className="rb:text-[12px] rb:text-[#5B6167]">{log.summary}</div>
                    </ContentWrapper>
                    : log.type === 'input_summary' && log.raw_results
                    ? <ContentWrapper>
                        <div className="rb:font-medium rb:text-[#212332] rb:mb-2">{log.query}</div>
                        <div className="rb:font-medium rb:text-[12px] rb:text-[#5B6167] rb:mb-2">{log.summary}</div>
                        <div className='rb:mt-2 rb:text-[12px] rb:text-[#5B6167]'>
                          {typeof log.raw_results === 'string'
                            ? <Markdown content={log.raw_results} />
                            : <>
                              {log.raw_results.reranked_results?.statements.length > 0 && log.raw_results.reranked_results?.statements.map((item: { statement: string; } , index: number) => (
                                <div key={index}>{item.statement}</div>
                              ))}
                              {log.raw_results.reranked_results?.summaries.length > 0 && log.raw_results.reranked_results?.summaries.map((item: { content: string; }, index: number) => (
                                <div key={index}>{item.content}</div>
                              ))}
                            </> 
                          }
                        </div>
                      </ContentWrapper>
                      : null
                    }
                  </ResultCard>
                ))}
              </Flex>
            }
          </RbCard>
        </Col>
      </Row>
    </>
  )
}

export default MemoryConversation