import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Flex, Button, Empty, Skeleton } from 'antd';
import { useTranslation } from 'react-i18next';

import type { LogDetailModalRef, LogItem } from '../types'
import RbModal from '@/components/RbModal'
import { getAppLogDetail, getPureWorkflowAppLogDetail } from '@/api/application'
import ChatContent from '@/components/Chat/ChatContent'
import { formatDateTime } from '@/utils/format'
import type { ChatItem } from '@/components/Chat/types'
import Runtime from '@/views/Workflow/components/Chat/Runtime'
import { nodeLibrary } from '@/views/Workflow/constant'
import type { Application } from '@/views/ApplicationManagement/types'

const PAGE_SIZE = 20

const nodeIconMap = Object.fromEntries(
  nodeLibrary.flatMap(c => c.nodes.map(n => [n.type, n.icon]))
)

/** Log detail data with conversation messages */
type Data = LogItem & {
  messages: ChatItem[];
  page?: {
    page: number;
    pagesize: number;
    total: number;
    hasnext: boolean;
  };
}

/** Modal component for displaying conversation log details */
const LogDetailModal = forwardRef<LogDetailModalRef, { source: string; appType: Application['type']; }>(({ source, appType }, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [vo, setVo] = useState<LogItem | null>(null)
  const [data, setData] = useState<Data>({} as Data)
  const [pageInfo, setPageInfo] = useState({ page: 1, hasNext: false })

  /** Close modal and reset form */
  const handleClose = () => {
    setVisible(false);
    setLoading(false)
    setLoadingEarlier(false)
    setVo(null)
    setData({} as Data)
    setPageInfo({ page: 1, hasNext: false })
  };

  /** Open modal */
  const handleOpen = (item: LogItem) => {
    setVisible(true);
    setVo(item)
  };

  /** Fetch detail when modal opens */
  useEffect(() => {
    if (visible && vo) {
      getDetail()
    }
  }, [visible, vo, appType])

  /** Process messages with node executions and interventions */
  const processMessages = (messages: ChatItem[], node_executions_map: any, pending_intervention: any): ChatItem[] => {
    let hasSubContentMessages = messages.map((item: any) => ({ ...item, status: item.role === 'user' ? null : item.status }))
    if (messages && messages.length > 0 && (node_executions_map && Object.keys(node_executions_map).length > 0 || pending_intervention && Object.keys(pending_intervention).length > 0)) {
      hasSubContentMessages = messages.map((item: any) => {
        if (item.id && (node_executions_map?.[item.id] || pending_intervention?.[item.id])) {
          const executions = node_executions_map?.[item.id] || []
          const interventions = pending_intervention?.[item.id]?.interventions || []
          const lastExecution = executions?.[executions.length - 1]
          const meta = lastExecution?.meta
          let sourceList: any[] = []
          
          if (meta?.sources?.length > 0 && (meta?.tool_type === 'knowledge_retrieval' || meta?.tool_type === 'skill')) {
            const groupedSources = meta?.sources.reduce((acc: any, source: any) => {
              const key = source.knowledge_name || source.knowledge_id || source.name || source.id || 'default';
              if (!acc[key]) {
                acc[key] = { ...source, name: source.knowledge_name || source.name, contentList: [source.content] };
              } else {
                acc[key].contentList.push(source.content);
              }
              return acc;
            }, {});
            sourceList = Object.values(groupedSources).map((group: any) => ({
              ...lastExecution,
              icon: nodeIconMap[lastExecution?.node_type],
              node_name: group.name,
              content: {
                input: lastExecution?.input || '',
                output: group.contentList.join('\n') || lastExecution?.output,
                error: lastExecution?.error
              }
            }));
          } else if (meta?.sources?.length > 0) {
            sourceList = meta?.sources?.map((source: any) => ({
              ...lastExecution,
              icon: nodeIconMap[lastExecution?.node_type],
              name: source.name || 'default',
              content: {
                input: lastExecution?.input || '',
                output: source.content || lastExecution?.output,
                error: lastExecution?.error
              }
            }));
          } else {
            sourceList = executions.map(({ input, output, cycle_items = [], error, process, ...node }: any) => {
              const converted: any = { ...node, icon: nodeIconMap[node.node_type], content: { input, output, process, error } }
              if (node.node_type === 'loop' && Array.isArray(cycle_items) && cycle_items.length > 0) {
                converted.subContent = cycle_items.map(({ input: cInput, output: cOutput, error: cError, process: cProcess, ...cNode }: any) => ({
                  ...cNode,
                  icon: nodeIconMap[cNode.node_type],
                  content: { input: cInput, output: cOutput, process: cProcess, error: cError }
                }))
              }
              return converted
            })
          }
          item.subContent = sourceList
          item.interventions = interventions
        }
        return { ...item }
      })
    }
    return hasSubContentMessages
  }

  /** Fetch conversation log detail from API (first page) */
  const getDetail = () => {
    if (!vo || !appType) return
    setLoading(true)
    const request = appType === 'pure_workflow' 
      ? getPureWorkflowAppLogDetail(vo.app_id, vo.execution_id) 
      : getAppLogDetail(vo.app_id, vo.id, { page: 1, pagesize: PAGE_SIZE })
    request.then(res => {
      const { node_executions_map, messages, pending_intervention, page, ...rest } = res as Data;
      const processedMessages = processMessages(messages, node_executions_map, pending_intervention)
      setData({
        ...rest,
        messages: processedMessages
      })
      if (page) {
        setPageInfo({ page: page.page, hasNext: page.hasnext })
      }
    })
    .finally(() => {
      setLoading(false)
    })
  }

  /** Load earlier messages (pagination) */
  const loadEarlier = () => {
    if (loadingEarlier || !pageInfo.hasNext || !vo || !appType) return
    setLoadingEarlier(true)
    const nextPage = pageInfo.page + 1
    const request = appType === 'pure_workflow' 
      ? getPureWorkflowAppLogDetail(vo.app_id, vo.execution_id)
      : getAppLogDetail(vo.app_id, vo.id, { page: nextPage, pagesize: PAGE_SIZE })
    
    request.then(res => {
      const { node_executions_map, messages, pending_intervention, page } = res as Data;
      const processedMessages = processMessages(messages, node_executions_map, pending_intervention)
      // Prepend older messages to the beginning
      setData(prev => ({
        ...prev,
        messages: [...processedMessages, ...(prev.messages || [])]
      }))
      if (page) {
        setPageInfo({ page: page.page, hasNext: page.hasnext })
      }
    })
    .finally(() => {
      setLoadingEarlier(false)
    })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const isPureWorkflow = appType === 'pure_workflow'

  return (
    <RbModal
      title={<>
        {data.title}
        <div className="rb:text-gray-600 rb:leading-4.5 rb:text-[12px]">{formatDateTime(data.created_at, 'YYYY.MM')} - {formatDateTime(data.updated_at, 'YYYY.MM')}</div>
      </>}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
    >
      <Flex justify={isPureWorkflow ? 'end' :"space-between"} align="center" className="rb:bg-gray-100 rb:rounded-lg rb:py-2.5! rb:pr-2.5! rb:pl-3.25!">
        {isPureWorkflow ? null : t('workingDetail.conversationStream')}
        <Flex gap={8}>
          <Button className="rb:h-6!" onClick={getDetail}>{t('workingDetail.refresh')}</Button>
        </Flex>
      </Flex>
      <div className="rb-border rb:p-3 rb:rounded-xl rb:mt-3 rb:h-[calc(100%-48px)] rb:overflow-y-auto">
      {loading
        ? <Skeleton active />
        : data.messages?.length === 0
          ? <Empty className="rb:my-20" />
          : (<>
            {pageInfo.hasNext && (
              <Flex justify="center">
                <Button
                  color="default" variant="filled"
                  className="rb:h-6! rb:text-[12px]! rb:text-gray-600! rb:mb-3!" 
                  onClick={loadEarlier}
                  loading={loadingEarlier}
                  disabled={loadingEarlier}
                >
                  {t('workingDetail.loadEarlier')}
                </Button>
              </Flex>
            )}
            <ChatContent
              contentClassNames="rb:max-w-110!"
              data={data.messages || []}
              streamLoading={false}
              labelFormat={(item) => formatDateTime(item.created_at)}
              renderRuntime={(item, index) => <Runtime source={source} item={item} index={index} />}
            />
          </>)
      }
      </div>
    </RbModal>
  );
});

export default LogDetailModal;