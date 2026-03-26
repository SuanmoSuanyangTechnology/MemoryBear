import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Select, Flex, Tooltip } from 'antd'
import { Node } from '@antv/x6'

import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import MappingList from '../MappingList'
import OutputList from './OutputList'
import CodeMirrorEditor from '@/components/CodeMirrorEditor';
import styles from './index.module.css'

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
    form.setFieldsValue({
      input_variables: [{ name: 'arg1' }, { name: 'arg2' }],
      code: codeTemplate[value as keyof typeof codeTemplate]
    })
  }

  return (
    <>
      <Form.Item name="input_variables">
        <MappingList 
          label={t('workflow.config.code.input_variables')} 
          name="input_variables" 
          options={options}
          valueKey="variable"
          extra={<Tooltip title={t('workflow.config.code.refreshTip')}>
            <div onClick={handleRefresh} className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/refresh.svg')]"></div>
          </Tooltip>}
        />
      </Form.Item>
      
      <Flex gap={4} vertical className="rb:border rb:bg-[#F6F6F6] rb:border-[#F6F6F6] rb:hover:bg-white rb:hover:border-[#171719] rb:pr-2! rb:rounded-md rb:py-1.5! rb:mb-4!">
        <Form.Item name="language" noStyle className=" rb:px-2!">
          <Select 
            options={[
              { label: 'PYTHON3', value: 'python3' },
              { label: 'JAVASCRIPT', value: 'javascript' }
            ]}
            popupMatchSelectWidth={false}
            className={`rb:font-medium! rb:w-25! rb:h-4! rb:p-0! ${styles.editor}`}
            onChange={handleChangeLanguage}
            variant="borderless"
          />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(prev, curr) => prev.language !== curr.language}>
          {() => (
            <Form.Item name="code" noStyle>
              <CodeMirrorEditor
                language={form.getFieldValue('language')}
                size="small"
              />
            </Form.Item>
          )}
        </Form.Item>
      </Flex>

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
