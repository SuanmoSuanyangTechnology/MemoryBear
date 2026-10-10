import type { ReactNode } from 'react'
import { Form, Space } from 'antd'
import { useTranslation } from 'react-i18next'

import Card from './Card'
import Editor from './Editor'

interface PromptVariableOption {
  label: string;
  value: string;
}

interface PromptConfigurationCardProps {
  title: ReactNode;
  promptLabel: ReactNode;
  promptDescription?: ReactNode;
  fieldName: string | Array<string | number>;
  variableOptions?: PromptVariableOption[];
  onPromptBlur?: (value?: string) => void;
  onAiPromptClick: () => void;
  formItemClassName?: string;
  editorClassName?: string;
  disabled?: boolean;
  children?: ReactNode;
}

/** Shared prompt editor card used by Agent and multi-agent supervisor configs. */
const PromptConfigurationCard = ({
  title,
  promptLabel,
  promptDescription,
  fieldName,
  variableOptions = [],
  onPromptBlur,
  onAiPromptClick,
  formItemClassName = 'rb:mb-0!',
  editorClassName = 'rb:h-50 rb:bg-white',
  disabled = false,
  children,
}: PromptConfigurationCardProps) => {
  const { t } = useTranslation()

  return (
    <Card
      title={title}
      extra={
        <Space
          size={1}
          className="rb:px-2 rb:h-5.5 rb:rounded-md rb:cursor-pointer rb:border rb:border-[rgba(21,94,239,0.3)] rb:text-[#155EEF]"
          onClick={onAiPromptClick}
        >
          <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/application/aiPrompt.png')]" />
          <span className="rb:font-[PingFangSC,PingFang_SC]!">{t('application.aiPrompt')}</span>
        </Space>
      }
    >
      <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2">
        <span className="rb:font-medium">{promptLabel}</span>
        {promptDescription && (
          <span className="rb:font-regular rb:text-gray-600"> ({promptDescription})</span>
        )}
      </div>

      <Form.Item name={fieldName} className={formItemClassName}>
        <Editor
          options={variableOptions}
          placeholder={t('application.promptPlaceholder')}
          className={editorClassName}
          onBlur={onPromptBlur}
          disabled={disabled}
        />
      </Form.Item>

      {children}
    </Card>
  )
}

export default PromptConfigurationCard
