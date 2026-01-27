import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Select, Space, Row, Col, Divider, Button, Tooltip } from 'antd'
import { Node } from '@antv/x6'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import MappingList from '../MappingList'
import Editor from '../../Editor'
import OutputList from './OutputList'

interface MappingItem {
  name?: string
  value?: string
}

interface CodeExecutionProps {
  options: Suggestion[]
  selectedNode: Node
}

const codeTemplate = {
  python3: `def main(arg1: str, arg2: str):
    return {
        "result": arg1 + arg2,
    }`,
  javascript: `function main({arg1, arg2}) {
    return {
        result: arg1 + arg2
    }
}`
}

const CodeExecution: FC<CodeExecutionProps> = ({ options }) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const values = Form.useWatch([], form) || {}

  const handleRefresh = () => {
    const code = form.getFieldValue('code') || ''
    const language = form.getFieldValue('language') || 'javascript'
    const currentInput = form.getFieldValue('input_variables') || []
    
    // Get input_variables names to replace in code
    const inputNames = currentInput.map((item: MappingItem) => item.name).filter(Boolean).join(', ')
    
    let newTemplate = code
    
    if (language === 'javascript') {
      // Replace function parameters: function name({arg1, arg2}) or function name(arg1, arg2)
      newTemplate = code.replace(
        /function(\s+\w+\s*\(\s*)(\{?)([^})]*)\}?(\s*\))/,
        (_match: string, prefix: string, brace: string, _params: string, suffix: string) => {
          return `function${prefix}${brace}${inputNames}${brace ? '}' : ''}${suffix}`
        }
      )
    } else if (language === 'python3') {
      // Replace Python function parameters: def name(arg1, arg2):
      newTemplate = code.replace(
        /def(\s+\w+\s*\()([^)]*)(\))/,
        (_match: string, prefix: string, _params: string, suffix: string) => {
          return `def${prefix}${inputNames}${suffix}`
        }
      )
    }
    
    form.setFieldValue('code', newTemplate)
  }
  const handleChangeLanguage = (value: string) => {
    form.setFieldValue('code', codeTemplate[value as keyof typeof codeTemplate])
    form.setFieldsValue({
      input_variables: [{ name: 'arg1' }, { name: 'arg2' }],
      code: codeTemplate[value as keyof typeof codeTemplate]
    })
  }

  return (
    <>
      <Form.Item name="input_variables" noStyle>
        <MappingList 
          label={t('workflow.config.code.input_variables')} 
          name="input_variables" 
          options={options}
          valueKey="variable"
          extra={<Tooltip title={t('workflow.config.code.refreshTip')}>
            <Button
              onClick={handleRefresh}
              className="rb:py-0! rb:px-1.5! rb:text-[12px]! rb:group"
              size="small"
            >
              <div onClick={handleRefresh} className="rb:size-3 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/refresh.svg')] rb:group-hover:bg-[url('@/assets/images/refresh_hover.svg')]"></div>
            </Button>
          </Tooltip>}
        />
      </Form.Item>
      
      <Space size={8} direction="vertical" className="rb:w-full rb:border rb:border-[#DFE4ED] rb:rounded-md rb:px-2 rb:py-1.5">
        <Row>
          <Col span={12}>
            <Form.Item name="language" noStyle>
              <Select 
                options={[
                  { label: 'PYTHON3', value: 'python3' },
                  { label: 'JAVASCRIPT', value: 'javascript' }
                ]}
                popupMatchSelectWidth={false}
                className="rb:font-medium!"
                onChange={handleChangeLanguage}
              />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="code" noStyle>
          <Editor size="small" language={values.language} />
        </Form.Item>
      </Space>
      
      <Divider />
      <Form.Item name="output_variables" noStyle>
        <OutputList
          label={t('workflow.config.code.output_variables')} 
          name="output_variables" 
        />
      </Form.Item>
    </>
  )
}

export default CodeExecution
