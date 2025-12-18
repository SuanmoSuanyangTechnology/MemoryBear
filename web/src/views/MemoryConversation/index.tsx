import { type FC, type ReactNode, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Col, Row, App, Skeleton, Space, Select, Flex } from 'antd'
import clsx from 'clsx'

import ConversationEmptyIcon from '@/assets/images/conversation/conversationEmpty.svg'
import AnalysisEmptyIcon from '@/assets/images/conversation/analysisEmpty.png'
import Card from './components/Card'
import { readService, getUserMemoryList } from '@/api/memory'
import Empty from '@/components/Empty'
import Markdown from '@/components/Markdown'
import type { Data } from '@/views/UserMemory/types'
import Chat from '@/components/Chat'
import MemoryFunctionIcon from '@/assets/images/conversation/memoryFunction.svg'
import OnlineIcon from '@/assets/images/conversation/online.svg'
import DeepThinkingIcon from '@/assets/images/conversation/deepThinking.svg'
import ButtonCheckbox from '@/components/ButtonCheckbox'
import DeepThinkingCheckedIcon from '@/assets/images/conversation/deepThinkingChecked.svg'
import OnlineCheckedIcon from '@/assets/images/conversation/onlineChecked.svg'
import MemoryFunctionCheckedIcon from '@/assets/images/conversation/memoryFunctionChecked.svg'
import type { ChatItem } from '@/components/Chat/types'
import dayjs from 'dayjs'
import type { AnyObject } from 'antd/es/_util/type';


const searchSwitchList = [
  {
    icon: DeepThinkingIcon,
    checkedIcon: DeepThinkingCheckedIcon,
    value: '0',
    label: 'deepThinking' // 深度思考
  },
  {
    icon: MemoryFunctionIcon,
    checkedIcon: MemoryFunctionCheckedIcon,
    value: '1',
    label: 'normalReply' // 普通回复
  },
  {
    icon: OnlineIcon,
    checkedIcon: OnlineCheckedIcon,
    value: '2',
    label: 'quickReply' // 快速回复
  },
]

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
  data?: DataItem[] | AnyObject;
  raw_results?: string | AnyObject;
  summary?: string;
  query?: string;
  reason?: string;
  result?: string;
  original_query: string;
  index?: number;
}

const ContentWrapper: FC<{ children: ReactNode }> = ({ children }) => (
  <div className="rb:p-3 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
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
    setChatData(prev => [...prev, { content: msg, created_at: new Date().getTime(), role: 'user' }])
    setLoading(true)
    readService({
      message: msg,
      group_id: userId,
      search_switch: search_switch,
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
                <Empty url={ConversationEmptyIcon} className="rb:h-full" size={[140, 100]} title={t('memoryConversation.conversationContentEmpty')} />
              }
              contentClassName='rb:h-[calc(100vh-362px)]'
              data={chatData}
              onChange={setMsg}
              onSend={handleSend}
              loading={loading}
              labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
            >
              <Flex gap={8}>
                {searchSwitchList.map(item => (
                  <ButtonCheckbox
                    key={item.value}
                    icon={item.icon}
                    checkedIcon={item.checkedIcon}
                    checked={search_switch === item.value}
                    onChange={() => handleChange(item.value)}
                  >
                    {t(`memoryConversation.${item.label}`)}
                  </ButtonCheckbox>
                ))}
              </Flex>
            </Chat>
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
                      `rb:p-[16px_24px] rb:rounded-lg`,
                      'rb:border rb:border-[#DFE4ED]',
                      {
                        'rb:shadow-[inset_4px_0px_0px_0px_#155EEF]': logIndex % 3 === 0,
                        'rb:shadow-[inset_4px_0px_0px_0px_#369F21]': logIndex % 3 === 1,
                        'rb:shadow-[inset_4px_0px_0px_0px_#9C6FFF]': logIndex % 3 === 2,
                      }
                    )}
                  >
                    <div className="rb:text-[16px] rb:font-medium rb:leading-[22px] rb:mb-6">{log.title}</div>
                    {log.type === 'problem_split' && Array.isArray(log.data) && log.data.length > 0 
                      ? <Space size={12} direction="vertical" style={{width: '100%'}}>
                        {log.data.map(vo => (
                          <ContentWrapper key={vo.id}>
                            <>
                              <div className="rb:font-medium rb:text-[#212332]">{vo.id}. {vo.question}</div>
                              <div className="rb:mt-2 rb:text-[12px] rb:text-[#5B6167]">{vo.reason}</div>
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
                                <div key={index} className="rb:mt-2 rb:text-[#5B6167] rb:text-[12px]">{item}</div>
                              ))}
                            </>
                          </ContentWrapper>
                        ))}
                      </Space>
                      : log.type === 'search_result' && log.raw_results
                      ? <ContentWrapper>
                        <div className="rb:font-medium rb:text-[#212332] rb:mb-2">{log.query}</div>
                          <div className='rb:mt-2 rb:text-[12px] rb:text-[#5B6167]'>
                            {typeof log.raw_results === 'string'
                              ? <Markdown content={log.raw_results} />
                              : <>
                                {log.raw_results.reranked_results?.statements.length > 0 && log.raw_results.reranked_results?.statements.map((item: { statement: string }, index: number) => (
                                  <div key={index}>{item.statement}</div>
                                ))}
                                {log.raw_results.reranked_results?.summaries.length > 0 && log.raw_results.reranked_results?.summaries.map((item: { content: string }, index: number) => (
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