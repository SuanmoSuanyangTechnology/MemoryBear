import { type FC } from 'react'
import { DatePicker, Flex, Select, Skeleton, Form, ConfigProvider, Pagination, Row, Col } from 'antd'
import { useTranslation } from 'react-i18next'
import dayjs from 'dayjs'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import { useEntityTimeline } from './useEntityTimeline'
import { timelineSortOptions as sortObj } from './entityTimelineTypes'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format'
import BtnTabs from '@/components/BtnTabs'

interface ExplicitNodePanelProps { userId: string }
/** Displays the current user's entity timeline with type, date and pagination filters. */
const ExplicitNodePanel: FC<ExplicitNodePanelProps> = ({ userId }) => {
  const { t } = useTranslation()
  const {
    activeTab: type, items, total, loading, typeStats, form,
    currentPagination, totalCount, handlePageChange, handleChangeTab, handleValuesChange,
  } = useEntityTimeline({
    end_user_id: userId,
    enabled: Boolean(userId),
    sourceKey: userId,
    initialTab: 'key_node',
    filterByType: true,
  })
  const counts = {
    key_node: typeStats.find(item => item.type === 'key_node')?.count || 0,
    memory_summary: typeStats.find(item => item.type === 'memory_summary')?.count || 0,
  }
  return (
    <RbCard
      title={<>
        <span className="rb:font-[MiSans-Bold] rb:font-bold">{t('explicitDetail.nodeMemory')}</span>
        <span className="rb:font-normal! rb:text-gray-600 rb:ml-1 rb:text-[13px]">{totalCount}</span>
      </>}
      headerType="borderless"
      headerClassName="rb:min-h-[53px]!"
      bodyClassName="rb:px-4! rb:py-0! rb:h-[calc(100%-53px)]!"
      className="rb:w-full! rb:h-full!"
    >
      <Flex vertical gap={12} className="rb:h-full! rb:overflow-hidden!">
        <Flex align="center" justify="space-between" gap={12}>
          <BtnTabs
            activeKey={type}
            items={(['key_node', 'memory_summary'] as const).map(key => ({
              key, label: `${t(`explicitDetail.${key}`)} ${counts[key]}`,
            }))}
            onChange={handleChangeTab}
            className="rb:shrink-0!"
          />

          <Form
            form={form}
            size="small"
            initialValues={{ sort: 'occurred_asc' }}
            onValuesChange={handleValuesChange}
          >
            <Flex gap={12} wrap justify="flex-end">
              <Form.Item name="sort" noStyle>
                <Select
                  placeholder={t('userMemory.sort')}
                  options={sortObj[type].map(value => ({
                    value,
                    label: t(`userMemory.${value}`)
                  }))}
                  labelRender={(props) => <span className="rb:text-[12px]">{props.label}</span>}
                  className="rb:w-40 rb:h-6.5!"
                  popupMatchSelectWidth={false}
                />
              </Form.Item>
              <ConfigProvider
                theme={{
                  components: {
                    DatePicker: {
                      inputFontSizeSM: 12,
                    },
                  },
                }}
              >
                <Form.Item name="timeRange" noStyle>
                  <DatePicker.RangePicker
                    placeholder={[t(`userMemory.${type}_time_start`), t(`userMemory.${type}_time_end`)]}
                    presets={[
                      {
                        label: t('userMemory.last7Days'),
                        value: [dayjs().subtract(7, 'days'), dayjs()],
                      },
                      {
                        label: t('userMemory.last30Days'),
                        value: [dayjs().subtract(30, 'days'), dayjs()],
                      },
                      {
                        label: t('userMemory.last1Year'),
                        value: [dayjs().subtract(1, 'year'), dayjs()],
                      },
                    ]}
                    className="rb:h-6.5! rb:w-62.5!"
                    suffixIcon={null}
                    allowEmpty={[true, true]}
                  />
                </Form.Item>
              </ConfigProvider>
            </Flex>
          </Form>
        </Flex>
        <div className="rb:flex-1 rb:min-h-0 rb:overflow-y-auto">
          {loading
            ? <Skeleton active />
            : !items.length
            ? <Empty size={88} className="rb:h-full!" />
            : (
              <Flex vertical className="rb:h-full!">
                <Flex vertical gap={16} className="rb:overflow-y-auto rb:flex-1!">
                  {items.map((vo, index) => (
                    <Row key={index} wrap={false} className="rb:flex rb:gap-5">
                      <Col flex="74px" className="rb:leading-4.5">
                        <Flex vertical gap={8} align="end"  className="rb:h-full! rb:text-[12px]">
                          <span className="rb:text-center rb:text-gray-600">{formatDateTime(vo.created_at, 'YYYY-MM-DD')}</span>
                          <div className={clsx("rb:flex-1 rb:w-px", {
                            'rb:bg-gray-500!': index !== items.length - 1
                          })} />
                        </Flex>
                      </Col>
                      <Col flex="1" className="rb:bg-gray-100 rb:rounded-xl rb:py-3! rb:px-4!">
                        <Flex gap={12} justify="space-between" align="center">
                          <Flex vertical gap={8}>
                            {vo.title
                              ? <div className="rb:font-medium rb:leading-5">{vo.title} </div>
                              : null
                            }
                            
                            <div className="rb:leading-5 rb:text-gray-600">
                              {vo.text}
                            </div>
                          </Flex>
                        </Flex>
                      </Col>
                    </Row>
                  ))}
                </Flex>
                <Flex justify="end" align="center" className="rb:h-13.5 rb:shrink-0">
                  <Pagination
                    current={currentPagination.page}
                    pageSize={currentPagination.pagesize}
                    total={total}
                    onChange={handlePageChange}
                    size="small"
                    showSizeChanger={true}
                  />
                </Flex>
              </Flex>
            )
          }
        </div>
      </Flex>
    </RbCard>
  )
}
export default ExplicitNodePanel
