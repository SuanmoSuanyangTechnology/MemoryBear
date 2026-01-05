import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Input, Select, Row, Col, Button, Tabs } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ToolItem, TimeToolModalRef } from '../types'
import RbModal from '@/components/RbModal';
import { execute } from '@/api/tools';
import { useI18n } from '@/store/locale'

const FormItem = Form.Item;

const tabKeys = ['currentTime', 'timestampConversion', 'timeFormat']
const formatList = [
  { label: 'YYYY-MM-DD HH:mm:ss', value: '%Y-%m-%d %H:%M:%S' },
  { label: 'YYYYMMDD_HHmmss', value: '%Y%m%d_%H%M%S' },
  { label: 'YYYY年MM月DD日 HH:mm', value: '%Y年%m月%d日 %H:%M' },
  { label: 'YYYY-MM-DD HH:mm:ss.SS', value: '%Y-%m-%d %H:%M:%S.%f' },
  { label: 'DD/MM/YYYY', value: '%d/%m/%Y' },
  { label: 'MM/DD/YYYY', value: '%m/%d/%Y' },
]
interface CurrentTimeObj {
  datetime: string;
  iso_format: string;
  timestamp: string;
  timestamp_ms: string;
}
const TimeToolModal = forwardRef<TimeToolModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const { timeZone } = useI18n()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<{ timestamp: string; formatType: string; }>();
  const [data, setData] = useState<ToolItem>({} as ToolItem)
  const [timeFormat, setTimeFormat] = useState<string | undefined>(undefined)
  const [activeTab, setActiveTab] = useState('currentTime');
  const values = Form.useWatch([], form)
  const [currentTime, setCurrentTime] = useState<CurrentTimeObj>({} as CurrentTimeObj)
  const [timestampFormat, setTimestampFormat] = useState<string | null>(null)

  const formatTabItems = () => {
    return tabKeys.map(key => ({
      key,
      label: t(`tool.${key}`),
    }))
  }
  const handleChangeTab = (key: string) => {
    setActiveTab(key);
    setTimestampFormat(null)
    setTimeFormat(undefined)
  }

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setData({} as ToolItem)
    setActiveTab('currentTime')
  };

  const handleOpen = (vo: ToolItem) => {
    setData(vo)
    setVisible(true);
    getCurrentTime(vo)
  };

  const getCurrentTime = (vo: ToolItem) => {
    if (!vo.id) return
    execute({
      tool_id: vo.id,
      parameters: {
        operation: 'now',
        output_format: '%Y-%m-%d %H:%M:%S',
      }
    }).then(res => {
      const response = res as { data: CurrentTimeObj}
      setCurrentTime(response.data)
    })
  }

  const handleFormat = () => {
    const timestamp = form.getFieldValue('timestamp')
    if (!timestamp || !data.id) return
    execute({
      "tool_id": data.id,
      "parameters": {
        "operation": "timestamp_to_datetime",
        "input_value": timestamp,
        "to_timezone": timeZone
      }
    })
    .then(res => {
      const response = res as { data: CurrentTimeObj }
      setTimestampFormat(response.data.datetime)
    })
  }
  const handleChangeFormatType = () => {
    if (!data.id) return
    execute({
      tool_id: data.id,
      parameters: {
        operation: 'now',
        output_format: values.formatType,
        to_timezone: timeZone
      }
    }).then(res => {
      const response = res as { data: CurrentTimeObj }
      console.log('timeFormat', response.data.datetime)
      setTimeFormat(response.data.datetime)
    })
  }

  useEffect(() => {
    if (values?.formatType && data.id && activeTab === 'timeFormat') {
      handleChangeFormatType()
    }
  }, [values?.formatType, data.id, activeTab, timeZone])

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));


  return (
    <RbModal
      title={data.name}
      open={visible}
      onCancel={handleClose}
      footer={null}
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          formatType: formatList[0].value
        }}
      >
        {/* 当前时间、时间戳转换、时间格式化 */}
        <Tabs 
          activeKey={activeTab} 
          items={formatTabItems()} 
          onChange={handleChangeTab}
        />
        
        {/* 当前时间 */}
        {activeTab === 'currentTime' &&
          <>
            <FormItem label={t('tool.currentTime')} >
              <Input disabled value={currentTime?.datetime} />
            </FormItem>
            <FormItem label={t('tool.utcTime')} >
              <Input disabled value={currentTime?.iso_format} />
            </FormItem>
            <FormItem label={t('tool.secondsTimestamp')} >
            <Input disabled value={currentTime?.timestamp} />
            </FormItem>
            <FormItem label={t('tool.millisecondsTimestamp')} >
              <Input disabled value={currentTime?.timestamp_ms} />
            </FormItem>
          </>
        }
        {/* 时间戳转换 */}
        {activeTab === 'timestampConversion' &&
          <>
            <FormItem label={t('tool.enterTimestamp')} >
              <Row gutter={24}>
                <Col span={16}>
                  <FormItem name="timestamp">
                    <Input />
                  </FormItem>
                </Col>
                <Col span={8}>
                  <Button onClick={handleFormat}>{t('tool.conversion')}</Button>
                </Col>
              </Row>
            </FormItem>
            {timestampFormat &&
              <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
                {t('tool.conversionResult')}
                <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:mt-2">
                  {timestampFormat}
                </div>
              </div>
            }
          </>
        }
        {/* 时间格式化 */}
        {activeTab === 'timeFormat' &&
          <>
            <FormItem label={t('tool.chooseFormatType')} name="formatType">
              <Select
                options={formatList}
              />
            </FormItem>
            <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
              {t('tool.conversionResult')}
              <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:mt-2">
                {timeFormat}
              </div>
            </div>
          </>
        }
      </Form>
    </RbModal>
  );
});

export default TimeToolModal;
