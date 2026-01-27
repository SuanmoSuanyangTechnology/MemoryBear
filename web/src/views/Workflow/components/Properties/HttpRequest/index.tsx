import { type FC, useRef, useState } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Row, Col, Select, Button, Divider, InputNumber, Switch, Input } from 'antd'
import { CaretDownOutlined, CaretRightOutlined, SettingOutlined } from '@ant-design/icons';
import Editor from '../../Editor'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import AuthConfigModal from './AuthConfigModal'
import type { AuthConfigModalRef, HttpRequestConfigForm } from './types'
import VariableSelect from "../VariableSelect";
import MessageEditor from '../MessageEditor'
import EditableTable from './EditableTable'

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

  const handleChangeBodyContentType = () => {
    form.setFieldValue(['body', 'data'], undefined)
  }

  const handleChangeErrorHandleMethod = (method: string) => {
    form.setFieldsValue({
      error_handle: {
        method,
        body: undefined,
        status_code: undefined,
        headers: undefined
      }
    })
    
    // 更新节点连接桩
    console.log('handleChangeErrorHandleMethod', selectedNode, graphRef?.current)
    if (selectedNode && graphRef?.current) {
      const existingPorts = selectedNode.getPorts();
      const errorPort = existingPorts.find((port: any) => port.id === 'ERROR');
      
      if (method === 'branch' && !errorPort) {
        // 添加异常节点连接桩
        selectedNode.addPort({
          id: 'ERROR',
          group: 'right',
          attrs: { text: { text: t('workflow.config.http-request.errorBranch'), fontSize: 12, fill: '#5B6167' }}
        });
      } else if (method !== 'branch' && errorPort) {
        // 移除异常节点连接桩和相关连线
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
      <div className="rb:flex rb:items-center rb:justify-between rb:mb-1">
        <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">API</div>
        <Button onClick={handleChangeAuth}
          size="small"
          type="text"
          icon={<SettingOutlined />}
          className="rb:mt-1 rb:text-[12px]!"
        >{t('workflow.config.http-request.auth')}: {!values?.auth?.auth_type || values?.auth?.auth_type === 'none' ? t('workflow.config.http-request.none') : t('workflow.config.http-request.apiKey')}</Button>
      </div>
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
        <Form.Item name={['body', 'content_type']}>
          <Select
            placeholder={t('common.pleaseSelect')}
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
              options={options.filter(vo => vo.dataType === 'string' || vo.dataType === 'number')}
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
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'binary' &&
          <Form.Item name={['body', 'data']} noStyle>
            <VariableSelect
              placeholder={t('common.pleaseSelect')}
              options={options.filter(vo => vo.dataType.includes('file'))}
              filterBooleanType={true}
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
        label={t('workflow.config.http-request.connect_timeout')}
        hidden={collapsed}
      >
        <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(['timeouts', 'connect_timeout'], value)}
        />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'read_timeout']}
        label={t('workflow.config.http-request.read_timeout')}
        hidden={collapsed}
      >
        <InputNumber
          placeholder={t('common.pleaseEnter')}
          className="rb:w-full!"
          onChange={(value) => form.setFieldValue(['timeouts', 'read_timeout'], value)}
        />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'write_timeout']}
        label={t('workflow.config.http-request.write_timeout')}
        hidden={collapsed}
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
            label={t('workflow.config.http-request.max_attempts')}
          >
            <InputNumber
              placeholder={t('common.pleaseEnter')}
              className="rb:w-full!"
              onChange={(value) => form.setFieldValue(['retry', 'max_attempts'], value)}
            />
          </Form.Item>
          <Form.Item
            name={['retry', 'retry_interval']}
            label={<>{t('workflow.config.http-request.retry_interval')} <span className="rb:text-[#5B6167]">(ms)</span></>}
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
      <Form.Item layout="horizontal" name={['error_handle', 'method']} label={t('workflow.config.http-request.error_handle')}>
        <Select
          placeholder={t('common.pleaseSelect')}
          onChange={handleChangeErrorHandleMethod}
          options={[
            { value: 'none', label: t('workflow.config.http-request.none') },
            { value: 'default', label: t('workflow.config.http-request.default') },
            { value: 'branch', label: t('workflow.config.http-request.branch') },
          ]}
        />
      </Form.Item>
      {values?.error_handle?.method === 'default' &&
        <>
          <Form.Item
            name={['error_handle', 'body']}
            label={<>body <span className="rb:text-[#5B6167] rb:ml-1">string</span></>}
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'status_code']}
            label={<>status_code <span className="rb:text-[#5B6167] rb:ml-1">number</span></>}
          >
            <InputNumber
              placeholder={t('common.pleaseEnter')}
              className="rb:w-full!"
              onChange={(value) => form.setFieldValue(['error_handle', 'status_code'], value)}
            />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'headers']}
            label={<>headers <span className="rb:text-[#5B6167] rb:ml-1">object</span></>}
          >
            <Input.TextArea placeholder={t('common.pleaseEnter')} />
          </Form.Item>
        </>
      }

      <AuthConfigModal 
        ref={authConfigModalRef}
        refresh={handleRefresh}
      />
    </>
  );
};
export default HttpRequest;