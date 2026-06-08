/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-01 14:43:33 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-02 17:18:57
 */
import { type FC, useMemo } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Row, Col, Select, Input, Divider, App, InputNumber } from 'antd'
import copy from 'copy-to-clipboard'

import EditableTable from './EditableTable'
import CodeMirrorEditor from '@/components/CodeMirrorEditor'

const queryParamsTypes = [
  { label: 'String', value: 'string' },
  { label: 'Number', value: 'number' },
  { label: 'Boolean', value: 'boolean' },
]
const contentTypes = [
  { label: 'application/json', value: 'application/json',
    types: [
      { label: 'String', value: 'string' },
      { label: 'Number', value: 'number' },
      { label: 'Boolean', value: 'boolean' },
      { label: 'Object', value: 'object' },
      { label: 'Array[String]', value: 'array[string]' },
      { label: 'Array[Number]', value: 'array[number]' },
      { label: 'Array[Boolean]', value: 'array[boolean]' },
      { label: 'Array[Object]', value: 'array[object]' },
    ]
  },
  { label: 'application/x-www-form-urlencoded', value: 'application/x-www-form-urlencoded',
    types: queryParamsTypes,
  },
  { label: 'text/plain', value: 'text/plain',
    types: [
      { label: 'String', value: 'string' },
    ],
  },
  { label: 'application/octet-stream', value: 'application/octet-stream',
    types: [
      { label: 'File', value: 'file' },
    ],
  },
  { label: 'multipart/form-data', value: 'multipart/form-data',
    types: [
      ...queryParamsTypes,
      { label: 'File', value: 'file' },
    ],
  },
]
const Webhook: FC<{ selectedNode?: any; graphRef?: any; }> = () => {
  const { t } = useTranslation()
  const { message } = App.useApp()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form) || {}
  console.log('Webhook', values)

  const path = useMemo(() => {
    return values?.route_key
      ? `${window.location.origin}/api/workflows/triggers/webhook/${values.route_key}`
      : undefined
  }, [values?.route_key])

  const handleCopy = () => {
    console.log('handleCopy', path)
    if (!path) {
      return
    }
    copy(path)
    message.success(t('common.copySuccess'))
  }
  const reqBodyTypes = useMemo(() => {
    return contentTypes.find(item => item.value === values?.content_type)?.types || []
  }, [values?.content_type])

  const handleChangeContentType = () => {
    form.setFieldValue('req_body_params', [])
  }
  return (
    <>
      <Form.Item
        label="WEBHOOK URL"
        className="rb:mb-0!"
      >
        <Row gutter={4}>
          <Col span={6}>
            <Form.Item name="method" noStyle>
              <Select
                options={[
                  { label: 'POST', value: 'POST' },
                  { label: 'GET', value: 'GET' },
                  { label: 'HEAD', value: 'HEAD' },
                  { label: 'PATCH', value: 'PATCH' },
                  { label: 'PUT', value: 'PUT' },
                  { label: 'DELETE', value: 'DELETE' },
                ]}
                className="rb:bg-transparent!"
                size="small"
              />
            </Form.Item>
          </Col>
          <Col span={18}>
            <Form.Item name="route_key" hidden>
            </Form.Item>
            <Input
              value={path}
              readOnly
              suffix={
                <div
                  className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]"
                  onClick={handleCopy}
                ></div>
              }
              className="rb:cursor-pointer"
              onClick={handleCopy}
            />
          </Col>
        </Row>
      </Form.Item>

      <Divider />

      <Form.Item name="content_type"  label={t('workflow.config.trigger.content_type')}>
        <Select
          options={contentTypes}
          className="rb:w-full!"
          size="small"
          onChange={handleChangeContentType}
        />
      </Form.Item>

      <Divider />

      <Form.Item name="query_params" noStyle>
        <EditableTable
          size="small"
          parentName="query_params"
          title="QUERY PARAMETERS"
          typeOptions={queryParamsTypes}
        />
      </Form.Item>

      <Divider />

      <Form.Item name="header_params" noStyle>
        <EditableTable
          size="small"
          parentName="header_params"
          title="HEADER PARAMETERS"
        />
      </Form.Item>

      <Divider />

      <Form.Item name="req_body_params" noStyle>
        <EditableTable
          size="small"
          parentName="req_body_params"
          title="REQUEST BODY PARAMETERS"
          typeOptions={reqBodyTypes}
        />
      </Form.Item>

      <Divider />

      <div className="rb:font-medium rb:text-[12px] rb:leading-4.5 rb:mb-2.5">
        {t('workflow.config.trigger.response')}
      </div>

      <Form.Item 
        label={t('workflow.config.trigger.status_code')}
        name={["response", 'status_code']}
        layout="horizontal"
      >
        <InputNumber
          size="small"
          className="rb:w-full!"
          min={200}
          max={399}
          onChange={(value) => form.setFieldValue(['response', 'status_code'], value || 200)}
        />
      </Form.Item>

      <Form.Item label={t('workflow.config.trigger.response_body')} className="rb:mb-0!">
        <Form.Item name={["response", 'body']} noStyle>
          <CodeMirrorEditor
            language="json"
            variant="outlined"
          />
        </Form.Item>
      </Form.Item>

      <Divider />
    </>
  );
};
export default Webhook;
