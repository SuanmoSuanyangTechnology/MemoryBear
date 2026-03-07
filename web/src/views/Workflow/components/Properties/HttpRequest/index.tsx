/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-09 18:35:43 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 15:20:32
 */
import { type FC, useRef, useState } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Row, Col, Select, Button, Divider, InputNumber, Switch, Input, Flex, Radio } from 'antd'
import { CaretDownOutlined, CaretRightOutlined, SettingOutlined } from '@ant-design/icons';

import Editor from '../../Editor'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import AuthConfigModal from './AuthConfigModal'
import type { AuthConfigModalRef, HttpRequestConfigForm } from './types'
import MessageEditor from '../MessageEditor'
import EditableTable from './EditableTable'
import { portTextAttrs, nodeWidth, portItemArgsY } from '../../../constant'

const HttpRequest: FC<{ options: Suggestion[]; selectedNode?: any; graphRef?: any; }> = ({
  options,
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const values = Form.useWatch([], form) || {}
  const authConfigModalRef = useRef<AuthConfigModalRef>(null)

  const handleChangeAuth = () => {
    authConfigModalRef.current?.handleOpen(values?.auth)
  }
  const handleRefresh = (auth: HttpRequestConfigForm['auth']) => {
    console.log('handleRefresh', auth)
    form.setFieldsValue({ auth })
  }

  const handleChangeBodyContentType = (e: any) => {
    const value = e.target.value || e.target.value
    form.setFieldValue(['body', 'data'], ['form-data', 'x-www-form-urlencoded'].includes(value) ? [{}] : undefined)
  }

  // Handle error handling method change and update node ports accordingly
  const handleChangeErrorHandleMethod = (method: string) => {
    form.setFieldsValue({
      error_handle: {
        method,
        body: undefined,
        status_code: undefined,
        headers: undefined
      }
    })
    
    // Update node ports
    console.log('handleChangeErrorHandleMethod', selectedNode, graphRef?.current)
    if (selectedNode && graphRef?.current) {
      const existingPorts = selectedNode.getPorts();
      const errorPort = existingPorts.find((port: any) => port.id === 'ERROR');
      
      if (method === 'branch' && !errorPort) {
        // Add error branch port
        selectedNode.addPort({
          id: 'ERROR',
          group: 'right',
          args: {
            x: nodeWidth,
            y: portItemArgsY + portItemArgsY,
          },
          attrs: { text: { text: t('workflow.config.http-request.errorBranch'), ...portTextAttrs }}
        });
      } else if (method !== 'branch' && errorPort) {
        // Remove error branch port and related edges
        const edges = graphRef.current.getEdges().filter((edge: any) => 
          edge.getSourceCellId() === selectedNode.id && edge.getSourcePortId() === 'ERROR'
        );
        edges.forEach((edge: any) => graphRef.current.removeCell(edge));
        selectedNode.removePort('ERROR');
      }
    }
  }

  const [collapsed, setCollapsed] = useState(true)
  const handleToggle = () => {
    setCollapsed((prev: boolean) => !prev)
  }

  return (
    <>
      <Flex align="center" justify="space-between" className="rb:mb-1!">
        <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">API</div>
        <Button onClick={handleChangeAuth}
          size="small"
          type="text"
          icon={<SettingOutlined />}
          className="rb:mt-1 rb:text-[12px]!"
        >{t('workflow.config.http-request.auth')}: {!values?.auth?.auth_type || values?.auth?.auth_type === 'none' ? t('workflow.config.http-request.none') : t('workflow.config.http-request.apiKey')}</Button>
      </Flex>
      <Row gutter={4}>
        <Col span={8}>
          <Form.Item name="method">
            <Select
              options={[
                { label: 'GET', value: 'GET' },
                { label: 'POST', value: 'POST' },
                { label: 'HEAD', value: 'HEAD' },
                { label: 'PATCH', value: 'PATCH' },
                { label: 'PUT', value: 'PUT' },
                { label: 'DELETE', value: 'DELETE' },
              ]}
              className="rb:bg-transparent!"
            />
          </Form.Item>
        </Col>
        <Col span={16}>
          <Form.Item name="url">
            <Editor 
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')} 
              variant="outlined"
              type="input"
              size="small"
              height={28}
            />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item name="auth" hidden>
      </Form.Item>

      <Form.Item name="headers" noStyle>
        <EditableTable
          size="small"
          parentName="headers"
          title="HEADERS"
          options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
        />
      </Form.Item>

      <Form.Item name="params" noStyle>
        <EditableTable
          size="small"
          parentName="params"
          title="PARAMS"
          options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
        />
      </Form.Item>

      <Form.Item label="BODY" className="rb:mb-0!">
        <Form.Item name={['body', 'content_type']} className="rb:mb-3!">
          <Radio.Group
            size="small"
            onChange={handleChangeBodyContentType}
            options={[
              { label: 'none', value: 'none' },
              { label: 'form-data', value: 'form-data' },
              { label: 'x-www-form-urlencoded', value: 'x-www-form-urlencoded' },
              { label: 'JSON', value: 'json' },
              { label: 'raw', value: 'raw' },
              { label: 'binary', value: 'binary' },
            ]}
          />
        </Form.Item>
        {values?.body?.content_type === 'form-data' &&
          <Form.Item name={['body', 'data']} noStyle>
            <EditableTable
              size="small"
              parentName={['body', 'data']}
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number' || vo.dataType.includes('file'))}
              typeOptions={[
                { label: 'text', value: 'text' },
                { label: 'file', value: 'file' }
              ]}
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'x-www-form-urlencoded' &&
          <Form.Item name={['body', 'data']} noStyle>
            <EditableTable
              size="small"
              parentName={['body', 'data']}
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
              filterBooleanType={true}
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'json' &&
          <Form.Item name={['body', 'data']} noStyle>
            <MessageEditor
              key="json"
              parentName={['body', 'data']}
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
              isArray={false}
              title="JSON"
              titleVariant="borderless"
              size="small"
              className="rb:bg-[#F6F6F6] rb:border-[#F6F6F6]! rb:hover:bg-white rb:hover:border-[#171719]!"
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'raw' &&
          <Form.Item name={['body', 'data']} noStyle>
            <MessageEditor
              key="raw"
              parentName={['body', 'data']}
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
              isArray={false}
              title="RAW TEXT"
              titleVariant="borderless"
              size="small"
              className="rb:bg-[#F6F6F6] rb:border-[#F6F6F6]! rb:hover:bg-white rb:hover:border-[#171719]!"
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'binary' &&
          <Form.Item name={['body', 'data']} noStyle>
            <Editor
              placeholder={t('common.pleaseSelect')}
              options={options.filter(vo => vo.dataType.includes('file'))}
              type="input"
              size="small"
            />
          </Form.Item>
        }
      </Form.Item>
      <Divider />
      <Form.Item layout="horizontal" name="verify_ssl" label={t('workflow.config.http-request.verify_ssl')} className="rb:mb-0!">
        <Switch />
      </Form.Item>

      <Divider />
      <div className="rb:font-medium rb:text-[12px] rb:leading-4.5 rb:mb-2.5 rb:cursor-pointer" onClick={handleToggle}>
        {t('workflow.config.http-request.timeouts')}
        {collapsed ? <CaretRightOutlined /> : <CaretDownOutlined />}
      </div>
      <Form.Item
        name={['timeouts', 'connect_timeout']}
        label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.connect_timeout')}</span>}
        hidden={collapsed}
        className="rb:mb-2!"
      >
        <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(['timeouts', 'connect_timeout'], value)}
        />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'read_timeout']}
        label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.read_timeout')}</span>}
        hidden={collapsed}
        className="rb:mb-2!"
      >
        <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(['timeouts', 'read_timeout'], value)}
        />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'write_timeout']}
        label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.write_timeout')}</span>}
        hidden={collapsed}
        className="rb:mb-2!"
      >
        <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(['timeouts', 'write_timeout'], value)}
        />
      </Form.Item>

      <Divider />
      <Form.Item name={['retry', 'enable']} valuePropName="checked" layout="horizontal" label={t('workflow.config.http-request.retry')}>
        <Switch />
      </Form.Item>
      {(values?.retry?.enable || typeof values?.retry?.max_attempts === 'number' || typeof values?.retry?.retry_interval === 'number') &&
        <>
          <Form.Item
            name={['retry', 'max_attempts']}
            label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.max_attempts')}</span>}
            className="rb:mb-2!"
          >
            <InputNumber
              placeholder={t('common.pleaseEnter')}
              className="rb:w-full!"
              onChange={(value) => form.setFieldValue(['retry', 'max_attempts'], value)}
            />
          </Form.Item>
          <Form.Item
            name={['retry', 'retry_interval']}
            label={<span className="rb:text-[#5B6167]">{t('workflow.config.http-request.retry_interval')}(ms)</span>}
            className="rb:mb-2!"
          >
            <InputNumber
              placeholder={t('common.pleaseEnter')}
              className="rb:w-full!"
              onChange={(value) => form.setFieldValue(['retry', 'retry_interval'], value)}
            />
          </Form.Item>
        </>
      }

      <Divider />
      <Flex justify="space-between" align="center">
        <div className="rb:text-[12px] rb:font-medium">{t('workflow.config.http-request.error_handle')}</div>
        <Form.Item layout="horizontal" name={['error_handle', 'method']} noStyle>
          <Select
            placeholder={t('common.pleaseSelect')}
            onChange={handleChangeErrorHandleMethod}
            options={[
              { value: 'none', label: t('workflow.config.http-request.none') },
              { value: 'default', label: t('workflow.config.http-request.default') },
              { value: 'branch', label: t('workflow.config.http-request.branch') },
            ]}
            className="rb:w-30!"
          />
        </Form.Item>
      </Flex>
      {values?.error_handle?.method === 'default' &&
        <>
          <Form.Item
            name={['error_handle', 'body']}
            label={<>
              <span className="rb:text-[#5B6167] rb:font-medium">body</span>
              <span className="rb:text-[#5B6167] rb:ml-1" style={{fontWeight: 400}}>string</span>
            </>}
            className="rb:my-2!"
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'status_code']}
            label={<>
              <span className="rb:text-[#5B6167] rb:font-medium">status_code</span>
              <span className="rb:text-[#5B6167] rb:ml-1" style={{fontWeight: 400}}>number</span>
            </>}
            className="rb:my-2!"
          >
            <InputNumber
              placeholder={t('common.pleaseEnter')}
              className="rb:w-full!"
              onChange={(value) => form.setFieldValue(['error_handle', 'status_code'], value)}
            />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'headers']}
            label={<>
              <span className="rb:text-[#5B6167] rb:font-medium">headers</span>
              <span className="rb:text-[#5B6167] rb:ml-1" style={{fontWeight: 400}}>object</span>
            </>}
            className="rb:my-2!"
          >
            <Input.TextArea placeholder={t('common.pleaseEnter')} />
          </Form.Item>
        </>
      }
      <Divider />

      <AuthConfigModal 
        ref={authConfigModalRef}
        refresh={handleRefresh}
      />
    </>
  );
};
export default HttpRequest;