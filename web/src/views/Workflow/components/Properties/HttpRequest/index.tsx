import { type FC, useRef } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Row, Col, Select, Button, Divider, InputNumber, Switch, Input } from 'antd'
import Editor from '../../Editor'
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import AuthConfigModal from './AuthConfigModal'
import type { AuthConfigModalRef, HttpRequestConfigForm } from './types'
import VariableSelect from "../VariableSelect";
import MessageEditor from '../MessageEditor'
import EditableTable from './EditableTable'

const HttpRequest: FC<{ options: Suggestion[]; }> = ({
  options,
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
    form.setFieldsValue({ auth: {...auth} })
  }

  const handleChangeBodyContentType = (contentType: string) => {
    const currentValues = form.getFieldsValue()
    form.setFieldsValue({
      body: {
        ...currentValues?.body,
        content_type: contentType,
        data: undefined
      }
    })
  }

  console.log('HttpRequest', values)

  return (
    <>
      <div className="rb:flex rb:items-center rb:justify-between rb:mb-4">
        <div>API</div>
        <Button onClick={handleChangeAuth}>{t('workflow.config.http-request.auth')}</Button>
      </div>
      <Row gutter={16}>
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
            />
          </Form.Item>
        </Col>
        <Col span={16}>
          <Form.Item name="url">
            <Editor options={options} variant="outlined" />
          </Form.Item>
        </Col>
      </Row>
      <Form.Item name="auth" hidden>
      </Form.Item>

      <Form.Item name="headers">
        <EditableTable
          parentName="headers"
          title="HEADERS"
          options={options}
        />
      </Form.Item>

      <Form.Item name="params">
        <EditableTable
          parentName="params"
          title="PARAMS"
          options={options}
        />
      </Form.Item>

      <Form.Item label="BODY">
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
              parentName={['body', 'data']}
              options={options}
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
              parentName={['body', 'data']}
              options={options}
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'json' &&
          <Form.Item name={['body', 'data']}>
            <MessageEditor
              options={options}
              isArray={false}
              title="JSON"
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'raw' &&
          <Form.Item name={['body', 'data']}>
            <MessageEditor
              options={options}
              isArray={false}
              title="RAW TEXT"
            />
          </Form.Item>
        }
        {values?.body?.content_type === 'binary' &&
          <Form.Item name={['body', 'data']}>
            <VariableSelect
              options={options}
            />
          </Form.Item>
        }
      </Form.Item>
      <Divider />
      <Form.Item layout="horizontal" name="verify_ssl" label={t('workflow.config.http-request.verify_ssl')}>
        <Switch />
      </Form.Item>

      <Divider />
      <div>{t('workflow.config.http-request.timeouts')}</div>
      <Form.Item
        name={['timeouts', 'connect_timeout']}
        label={t('workflow.config.http-request.connect_timeout')}
      >
        <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'read_timeout']}
        label={t('workflow.config.http-request.read_timeout')}
      >
        <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
      </Form.Item>
      <Form.Item
        name={['timeouts', 'write_timeout']}
        label={t('workflow.config.http-request.write_timeout')}
      >
        <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
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
            <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
          </Form.Item>
          <Form.Item
            name={['retry', 'retry_interval']}
            label={t('workflow.config.http-request.retry_interval')}
          >
            <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
          </Form.Item>
        </>
      }

      <Divider />
      <Form.Item layout="horizontal" name={['error_handle', 'method']} label={t('workflow.config.http-request.error_handle')}>
        <Select
          placeholder={t('common.pleaseSelect')}
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
            label="body"
          >
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'status_code']}
            label="status_code"
          >
            <InputNumber placeholder={t('common.pleaseEnter')} className="rb:w-full!" />
          </Form.Item>
          <Form.Item
            name={['error_handle', 'headers']}
            label="headers"
            rules={[
              {
                validator: (_, value) => {
                  if (!value) return Promise.resolve();
                  try {
                    JSON.parse(value);
                    return Promise.resolve();
                  } catch {
                    return Promise.reject(new Error('Please enter valid JSON format'));
                  }
                }
              }
            ]}
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