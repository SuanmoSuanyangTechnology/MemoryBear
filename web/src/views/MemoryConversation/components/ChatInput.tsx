import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex, Input, Form } from 'antd'
import MemoryFunctionIcon from '@/assets/images/conversation/memoryFunction.svg'
import OnlineIcon from '@/assets/images/conversation/online.svg'
import DeepThinkingIcon from '@/assets/images/conversation/deepThinking.svg'
import SendIcon from '@/assets/images/conversation/send.svg'
import SendDisabledIcon from '@/assets/images/conversation/sendDisabled.svg'
import ButtonCheckbox from '@/components/ButtonCheckbox'
import DeepThinkingCheckedIcon from '@/assets/images/conversation/deepThinkingChecked.svg'
import OnlineCheckedIcon from '@/assets/images/conversation/onlineChecked.svg'
import MemoryFunctionCheckedIcon from '@/assets/images/conversation/memoryFunctionChecked.svg'
import LoadingIcon from '@/assets/images/conversation/loading.svg'
import type { TestParams } from '../index'

interface ChatInputProps {
  query?: TestParams;
  onChange: (query: TestParams) => void;
  onSend: () => void;
  loading: boolean;
  source: 'conversation' | 'memory';
}
const searchSwitchList = [
  {
    icon: DeepThinkingIcon,
    checkedIcon: DeepThinkingCheckedIcon,
    value: '0',
    label: 'deepThinking' // 深度思考
  },
  {
    icon: MemoryFunctionIcon,
    checkedIcon: MemoryFunctionCheckedIcon,
    value: '1',
    label: 'normalReply' // 普通回复
  },
  {
    icon: OnlineIcon,
    checkedIcon: OnlineCheckedIcon,
    value: '2',
    label: 'quickReply' // 快速回复
  },
]

const ChatInput: FC<ChatInputProps> = ({ source,query, onChange, onSend, loading }) => {
  const [form] = Form.useForm()
  const { t } = useTranslation();
  const values = Form.useWatch([], form);
  const [search_switch, setSearchSwitch] = useState('0')

  useEffect(() => {
    if (onChange) {
      onChange({...values, search_switch})
    }
  }, [values, search_switch, onChange])
  useEffect(() => {
    if (!query?.message) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [form, query?.message])
  useEffect(() => {
    if (loading) {
      form.setFieldsValue({
        message: undefined,
      })
    }
  }, [loading])

  const handleChange = (value: string) => {
    form.setFieldsValue({
      search_switch: value,
    })
    setSearchSwitch(value)
  }

  return (
    <Form form={form} layout="vertical" className="rb:absolute rb:bottom-[12px] rb:left-0 rb:right-0">
      <Flex vertical justify="space-between" className="rb:border rb:border-[#DFE4ED] rb:rounded-[12px] rb:min-h-[120px]">
        <Form.Item name="message" className="rb:mb-[0]!">
          <Input.TextArea
            className="rb:m-[10px_12px_10px_12px]! rb:p-[0]! rb:w-[calc(100%-24px)]! rb:flex-[1_1_auto]"
            // rows={4}
            variant="borderless"
            autoSize={{ minRows: 2, maxRows: 2 }}
            onChange={(e) => onChange({ ...query, message: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && e.target.value?.trim() !== '' && !loading) {
                e.preventDefault();
                onSend();
              }
            }}
          />
        </Form.Item>

        <Flex align="center" justify="space-between" className="rb:m-[0_10px_10px_10px]!">
          {source === 'memory' &&
            <Flex gap={8}>
              {searchSwitchList.map(item => (
                <ButtonCheckbox
                  key={item.value}
                  icon={item.icon}
                  checkedIcon={item.checkedIcon}
                  checked={search_switch === item.value}
                  onChange={() => handleChange(item.value)}
                >
                  {t(`memoryConversation.${item.label}`)}
                </ButtonCheckbox>
              ))}
            </Flex>
          }
          {source === 'conversation' &&
            <Flex gap={8}>
              <Form.Item name="web_search" valuePropName="checked" className="rb:mb-[0]!">
                <ButtonCheckbox
                  icon={OnlineIcon}
                  checkedIcon={OnlineCheckedIcon}
                >
                  {t(`memoryConversation.web_search`)}
                </ButtonCheckbox>
              </Form.Item>
              <Form.Item name="memory" valuePropName="checked" className="rb:mb-[0]!">
                <ButtonCheckbox
                  icon={MemoryFunctionIcon}
                  checkedIcon={MemoryFunctionCheckedIcon}
                >
                  {t(`memoryConversation.memory`)}
                </ButtonCheckbox>
              </Form.Item>
            </Flex>
          }
          {loading ? <img src={LoadingIcon} className="rb:w-[22px] rb:h-[22px] rb:cursor-pointer" />:
            !values || !values?.message || values?.message?.trim() === '' ?
            <img src={SendDisabledIcon} className="rb:w-[22px] rb:h-[22px] rb:cursor-pointer" />
            : <img src={SendIcon} className="rb:w-[22px] rb:h-[22px] rb:cursor-pointer" onClick={onSend} />
          }
        </Flex>
      </Flex>
    </Form>
  )
}

export default ChatInput
