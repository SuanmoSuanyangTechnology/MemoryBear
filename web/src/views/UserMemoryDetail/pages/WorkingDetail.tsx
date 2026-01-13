import { type FC, useEffect, useState, useMemo } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Select, Form, Space, Skeleton, Input, Button, Divider } from 'antd'
import RbCard from '@/components/RbCard/Card'
import {
  getConversations,
  getConversationMessages,
  getConversationDetail,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import RbAlert from '@/components/RbAlert'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import PageLoading from '@/components/Empty/PageLoading'

interface Conversation {
  title: string;
  id: string;
}
interface Detail {
  theme: string;
  theme_intro: string;
  summary: string;
  question: string[];
  takeaways: string[];
  info_score: number;
}

const WorkingDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Conversation[]>([])
  const [messagesLoading, setMessagesLoading] = useState<boolean>(false)
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [selected, setSelected] = useState<Conversation | null>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    setSelected(null)
    setDetail(null)
    setData([])
    getConversations(id).then((res) => {
      const response = res as Conversation[] 
      setData(response)
      setSelected(response[0] || null)
    })
    .finally(() => {
      setLoading(false)
    })
  }

  useEffect(() => {
    if (!id || !selected || !selected.id) return
    getDetail(selected.id)
  }, [id, selected])

  const getDetail = (conversationId: string) => {
    if (!id || !conversationId) return

    setDetail(null)
    setMessages([])
    setDetailLoading(true)
    setMessagesLoading(true)
    getConversationMessages(id, conversationId)
      .then(res => {
        setMessages(res as ChatItem[])
      })
      .finally(() => {
        setMessagesLoading(false)
      })
    getConversationDetail(id, conversationId)
      .then(res => {
        setDetail(res as Detail)
      })
      .finally(() => {
        setDetailLoading(false)
      })
  }
  const timeRange = useMemo(() => {
    const times = messages.filter(m => m.created_at).map(m => Number(m.created_at))
    if (times.length === 0) return ''
    const minTime = Math.min(...times)
    const maxTime = Math.max(...times)
    return `${formatDateTime(minTime, 'YYYY.MM')} - ${formatDateTime(maxTime, 'YYYY.MM')}`
  }, [messages])

  return (
    <div className="rb:h-[calc(100vh-64px)]! rb:w-full rb:-mx-4 rb:-my-3">
      {loading
        ? <PageLoading />
        : data.length === 0
        ? <Empty />
        :(
          <Row gutter={16} className="rb:h-full">
            <Col span={5}>
              <div className="rb:h-full! rb:border-r rb:border-[#EAECEE] rb:py-3 rb:px-4">
                {data.map(item => (
                  <div key={item.id} className="rb:mb-3">
                    <div className={clsx("rb:p-[8px_13px] rb:rounded-lg rb:leading-5 rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                      'rb:bg-[#FFFFFF] rb:shadow-[0px_2px_4px_0px_rgba(0,0,0,0.15)] rb:font-medium rb:hover:bg-[#FFFFFF]!': item.id === selected?.id,
                    })}
                      onClick={() => setSelected(item)}
                    >
                      {item.title}
                    </div>
                  </div>
                ))}
              </div>
            </Col>
            {selected && <>
              <Col span={19}>
                <div className="rb:text-[18px] rb:font-medium rb:leading-6 rb:mt-4">{selected.title}</div>
                <div className="rb:mt-1 rb:text-[#5B6167] rb:leading-5">{timeRange}</div>

                <Row gutter={16}>
                  <Col span={16}>
                    <RbCard
                      title={t('workingDetail.conversationStream')}
                      extra={<Button className="rb:h-6!" onClick={() => getDetail(selected.id)}>{t('workingDetail.refresh')}</Button>}
                      className="rb:mt-4!"
                      headerClassName='rb:bg-[#F6F8FC]! rb:border-b! rb:border-b-[#DFE4ED]! rb:min-h-11!'
                      headerType="borderless"
                      bodyClassName="rb:h-[calc(100vh-210px)]"
                    >
                      {messagesLoading
                        ? <Skeleton active />
                        : messages.length === 0
                        ? <Empty />
                        : (
                          <ChatContent
                            classNames="rb:h-[calc(100vh-244px)]"
                            data={messages}
                            streamLoading={false}
                            labelFormat={(item) => formatDateTime(item.created_at)}
                          />
                        )
                      }
                    </RbCard>
                  </Col>
                  <Col span={8}>
                    <RbCard className="rb:mt-4!" bodyClassName="rb:h-[calc(100vh-166px)] rb:overflow-y-auto">
                      {detailLoading
                        ? <Skeleton active />
                        : detail
                        ? <>
                          <>
                            <div className="rb:text-[#369F21] rb:font-medium rb:text-[18px] rb:leading-4 rb:mb-3">{t('workingDetail.successfulTitle')}</div>

                            {detail.takeaways.length > 0
                              ? (
                                <ul className="rb:text-[#5B6167] rb:leading-5.5 rb:list-disc rb:ml-4">
                                  {detail.takeaways.map(vo => <li>{vo}</li>)}
                                </ul>
                              )
                              : <Empty size={88} />
                            }
                          </>

                          <>
                            <Divider />
                            <div className="rb:text-[#FF5D34] rb:font-medium rb:text-[18px] rb:leading-4 rb:mb-3">{t('workingDetail.question')}</div>

                            {detail.question.length > 0
                              ? (
                                <ul className="rb:text-[#5B6167] rb:leading-5.5 rb:list-disc rb:ml-4">
                                  {detail.question.map(vo => <li>{vo}</li>)}
                                </ul>
                              )
                              : <Empty size={88} />
                            }
                          </>

                          <>
                            <Divider />
                            <div className="rb:text-[#369F21] rb:font-medium rb:text-[18px] rb:leading-4 rb:mb-3">{t('workingDetail.summary')}</div>
                            {detail.summary
                              ? <RbAlert className="rb:text-[#212332]! rb:text-[14px]! rb:leading-5.5! rb:p-3!">{detail.summary}</RbAlert>
                              : <Empty size={88} />
                            }
                          </>
                        </>
                        : <Empty />
                      }
                    </RbCard>
                  </Col>
                </Row>
              </Col>
            </>}
          </Row>
        )
      }
    </div>
  )
}
export default WorkingDetail