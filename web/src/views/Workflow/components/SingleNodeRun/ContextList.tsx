/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-07 18:37:15 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-07 18:37:15 
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Flex, Form } from 'antd'

import CodeMirrorEditor from '@/components/CodeMirrorEditor'

const defaultContextItem = {
  "content": "",
  "title": "",
  "url": "",
  "icon": "",
  "metadata": {
    "dataset_id": "",
    "dataset_name": "",
    "document_id": [],
    "document_name": "",
    "document_data_source_type": "",
    "segment_id": "",
    "segment_position": "",
    "segment_word_count": "",
    "segment_hit_count": "",
    "segment_index_node_hash": "",
    "score": ""
  }
}

const ContextList: FC = () => {
  const { t } = useTranslation()

  return (
    <Form.List name="context" initialValue={[JSON.stringify(defaultContextItem, null, 2)]}>
      {(fields, { add, remove }) => (
        <Flex vertical gap={8}>
          <Flex justify="space-between" align="center">
            <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">{t('workflow.config.llm.context')}</div>
            <Button
              onClick={() => add(JSON.stringify(defaultContextItem, null, 2))}
              size="small"
              className="rb:text-[12px]! rb:rounded-sm!"
            >
              + {t('common.add')}
            </Button>
          </Flex>
          {fields.map(({ key, name }) => (
            <Flex vertical gap={4} key={key} className="rb:py-1! rb:bg-[#F6F6F6] rb:rounded-lg rb:text-[12px]">
              <Flex justify="space-between" align="center" className="rb:font-medium rb:px-2!">
                <span>JSON</span>
                <div
                  className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/deleteBg.svg')] rb:hover:bg-[url('@/assets/images/workflow/deleteBg_hover.svg')]"
                  onClick={() => remove(name)}
                ></div>
              </Flex>
              <Form.Item name={name} noStyle>
                <CodeMirrorEditor
                  language="json"
                  size="small"
                  variant="filled"
                />
              </Form.Item>
            </Flex>
          ))}
        </Flex>
      )}
    </Form.List>
  )
}

export default ContextList
