import { type FC, type ReactNode, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Col, Row, App, Skeleton, Space, Select } from 'antd'
import clsx from 'clsx'

import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.png'
import Card from './components/Card'
import Chat from './components/Chat'
import { readService, getUserMemoryList } from '@/api/memory'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import type { Data } from '@/views/UserMemory/types'

export interface TestParams {
  group_id: string;
  message: string;
  search_switch: string;
  history: { role: string; content: string }[];
  web_search?: boolean;
  memory?: boolean;
  conversation_id?: string;
}
interface DataItem {
    id: string;
    question: string;
    type: string;
    reason: string;
  }
export interface LogItem {
  type: string;
  title: string;
  data?: DataItem[] | Record<string, string>;
  raw_results?: string;
  summary?: string;
  query?: string;
  reason?: string;
  result?: string;
  original_query: string;
  index?: number;
}

const ContentWrapper: FC<{ children: ReactNode }> = ({ children }) => (
  <div className="rb:p-[12px] rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-[8px]">
    {children}
  </div>
)

const MemoryConversation: FC = () => {
  const { t } = useTranslation()
  const { message } = App.useApp();
  const [query, setQuery] = useState<TestParams>({
    group_id: '',
    message: '',
    search_switch: '0',
    history: [],
  })
  const [userId, setUserId] = useState<string>()
  const [loading, setLoading] = useState<boolean>(false)
  const [chatData, setChatData] = useState<{ content: string; created_at: string | number; role: string }[]>([])
  const [logs, setLogs] = useState<LogItem[]>([])
  const [userList, setUserList] = useState<Data[]>([])

  useEffect(() => {
    getUserMemoryList().then(res => {
      setUserList((res as Data[] || []).map(item => ({
        ...item,
        name: item.end_user?.other_name && item.end_user?.other_name !== '' ? item.end_user?.other_name : item.end_user?.id
      })))
    })
  }, [])

  const handleSend = () => {
    if(!userId) {
      message.warning(t('common.inputPlaceholder', { title: t('memoryConversation.userID') }))
      return
    }
    setChatData(prev => [...prev, { content: query.message || '', created_at: new Date().getTime(), role: 'user' }])
    setLoading(true)
    readService({
      ...query,
      group_id: userId,
      history: [],
    })
      .then(res => {
        const response = res as { answer: string; intermediate_outputs: LogItem[] }
        setChatData(prev => [...prev, { content: response.answer || '-', created_at: new Date().getTime(), role: 'assistant' }])
        setLogs(response.intermediate_outputs)
      })
      .finally(() => {
        setLoading(false)
      })
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
            filterOption={(inputValue, option) => option.label?.toLowerCase().indexOf(inputValue.toLowerCase()) !== -1}
            showSearch={true}
            // filterOption={(inputValue, option) => option.label?.toLowerCase().indexOf(inputValue.toLowerCase()) !== -1}
            placeholder={t('memoryConversation.searchPlaceholder')}
            style={{ width: '100%', marginBottom: '16px' }}
            onChange={setUserId}
          />
        </Col>
      </Row>
      <Row gutter={16} className="rb:h-[calc(100vh-152px)] rb:overflow-hidden">
        <Col span={12}>
          <Card 
            title={t('memoryConversation.conversationContent')}
            bodyClassName="rb:pb-[0]!"
          >
            <Chat
              empty={
                <Empty url={ConversationEmptyIcon} size={[140, 100]} title={t('memoryConversation.conversationContentEmpty')} />
              }
              data={chatData}
              query={query}
              onChange={setQuery}
              onSend={handleSend}
              loading={loading}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card 
            title={t('memoryConversation.memoryConversationAnalysis')}
            bodyClassName='rb:overflow-auto'
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
            : <Space size={12} direction="vertical" style={{width: '100%'}}>
                {logs.map((log, logIndex) => (
                  <div key={logIndex}
                    className={clsx(
                      `rb:p-[16px_24px] rb:rounded-[8px]`,
                      'rb:border-[1px] rb:border-[#DFE4ED]',
                      {
                        'rb:shadow-[inset_4px_0px_0px_0px_#155EEF]': logIndex % 3 === 0,
                        'rb:shadow-[inset_4px_0px_0px_0px_#369F21]': logIndex % 3 === 1,
                        'rb:shadow-[inset_4px_0px_0px_0px_#9C6FFF]': logIndex % 3 === 2,
                      }
                    )}
                  >
                    <div className="rb:text-[16px] rb:font-medium rb:leading-[22px] rb:mb-[24px]">{log.title}</div>
                    {log.type === 'problem_split' && Array.isArray(log.data) && log.data.length > 0 
                      ? <Space size={12} direction="vertical" style={{width: '100%'}}>
                        {log.data.map(vo => (
                          <ContentWrapper key={vo.id}>
                            <>
                              <div className="rb:font-medium rb:text-[#212332]">{vo.id}. {vo.question}</div>
                              <div className="rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167]">{vo.reason}</div>
                            </>
                          </ContentWrapper>
                        ))}
                      </Space>
                      : log.type === 'problem_extension' && log.data && Object.keys(log.data).length > 0 
                      ? <Space size={12} direction="vertical" style={{width: '100%'}}>
                        {Object.keys(log.data).map((key: string) => (
                          <ContentWrapper key={key}>
                            <>
                              <div className="rb:font-medium rb:text-[#212332]">{key}</div>
                              {(log.data as Record<string, string[]>)[key].map((item, index) => (
                                <div key={index} className="rb:mt-[8px] rb:text-[#5B6167] rb:text-[12px]">{item}</div>
                              ))}
                            </>
                          </ContentWrapper>
                        ))}
                      </Space>
                      : log.type === 'search_result' && log.raw_results
                      ? <ContentWrapper>
                        <div className="rb:font-medium rb:text-[#212332] rb:mb-[8px]">{log.query}</div>
                          <div className='rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167]'>
                            {typeof log.raw_results === 'string'
                              ? <Markdown content={log.raw_results} />
                              : <>
                                {log.raw_results.reranked_results?.statements.length > 0 && log.raw_results.reranked_results?.statements.map((item, index) => (
                                  <div key={index}>{item.statement}</div>
                                ))}
                                {log.raw_results.reranked_results?.summaries.length > 0 && log.raw_results.reranked_results?.summaries.map((item, index) => (
                                  <div key={index}>{item.content}</div>
                                ))}
                              </> 
                            }
                          </div>
                        </ContentWrapper>
                      : log.type === 'retrieval_summary' && log.summary
                      ? <ContentWrapper><div className="rb:text-[12px] rb:text-[#5B6167]">{log.summary}</div></ContentWrapper>
                      : log.type === 'verification'
                      ? <ContentWrapper>
                        <div className="rb:font-medium rb:text-[#212332]">{log.query}</div>
                        <div className="rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167]">{log.reason}</div>
                        <div className="rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167]">{log.result}</div>
                      </ContentWrapper>
                      : log.type === 'output_type'
                      ? <ContentWrapper>
                        <div className="rb:font-medium rb:text-[#212332] rb:mb-[8px]">{log.query}</div>
                        <div className="rb:text-[12px] rb:text-[#5B6167]">{log.summary}</div>
                      </ContentWrapper>
                      : log.type === 'input_summary' && log.raw_results
                      ? <ContentWrapper>
                          <div className="rb:font-medium rb:text-[#212332] rb:mb-[8px]">{log.query}</div>
                          <div className="rb:font-medium rb:text-[12px] rb:text-[#5B6167] rb:mb-[8px]">{log.summary}</div>
                          <div className='rb:mt-[8px] rb:text-[12px] rb:text-[#5B6167]'>
                            {typeof log.raw_results === 'string'
                              ? <Markdown content={log.raw_results} />
                              : <>
                                {log.raw_results.reranked_results?.statements.length > 0 && log.raw_results.reranked_results?.statements.map((item, index) => (
                                  <div key={index}>{item.statement}</div>
                                ))}
                                {log.raw_results.reranked_results?.summaries.length > 0 && log.raw_results.reranked_results?.summaries.map((item, index) => (
                                  <div key={index}>{item.content}</div>
                                ))}
                              </> 
                            }
                          </div>
                        </ContentWrapper>
                      : null
                    }
                  </div>
                ))}
              </Space>}
          </Card>
        </Col>
      </Row>
    </>
  )
}

export default MemoryConversation