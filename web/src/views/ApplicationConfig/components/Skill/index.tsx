/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:42:56 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:42:56 
 */
import { useEffect, type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Switch, Form, Flex } from 'antd'

import type {
  SkillConfigForm,
} from './types'
import Card from '../Card'
import SkillsItem from './SkillsItem'
import { getSkillList } from '@/api/skill'
import type { Skill } from '@/views/Skills/types'

/**
 * Process flow steps for skill execution
 * Defines the sequential steps in the skill execution workflow
 */
const processObj = [
  'receiveTask',      // Step 1: Receive task
  'analyTask',        // Step 2: Analyze task intent
  'dynamicMatchSkill', // Step 3: Dynamically match appropriate skill
  'executeTask'       // Step 4: Execute the task
]

/**
 * Skill Configuration Component
 * 
 * Main component for managing agent skill configuration including:
 * - Enabling/disabling skill functionality
 * - Configuring dynamic skill binding
 * - Displaying skill execution process flow
 * - Managing skill selection and assignment
 * 
 * @param value - Current skill configuration values
 * @param onChange - Callback function when configuration changes
 */
const SkillList: FC<{value?: SkillConfigForm; onChange?: (config: SkillConfigForm) => void}> = () => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const skillConfig = Form.useWatch(['skills'], form)

  /**
   * Effect: Fetch and populate skill details for skills without names
   * Ensures all selected skills have complete information by fetching from API
   */
  useEffect(() => {
    const { skill_ids = [] } = skillConfig || {}

    // Filter skills that don't have name property
    const skillsWithoutName = skill_ids.filter((vo: Skill) => !vo.name)

    if (skillsWithoutName.length > 0) {
      getSkillList({ page: 1, pagesize: 100 })
        .then(res => {
          const response = res as { items: Skill[] }
          // Create a map of skill ID to skill object for quick lookup
          const map = response.items.reduce((prev: any, curr: any) => {
            prev[curr.id] = curr
            return prev
          }, {})

          // Merge fetched skill details with existing skill IDs
          const newSkillIds = skill_ids.map((vo: any) => {
            return {
              ...vo,
              ...map[vo.id]
            }
          })

          form.setFieldValue(['skills', 'skill_ids'], newSkillIds)
        })
    }

  }, [skillConfig?.skill_ids])

  /**
   * Effect: Reset skill configuration when skill functionality is disabled
   * Clears all_skills flag and skill_ids array when enabled is set to false
   */
  useEffect(() => {
    if (!skillConfig?.enabled) {
      form.setFieldValue('skills', {
        ...skillConfig,
        all_skills: false,
        skill_ids: []
      })
    }
  }, [skillConfig?.enabled])


  return (
    <Card
      title={<>
        {t('application.skill')}
        <span className="rb:font-regular rb:text-[12px] rb:text-[#5B6167]"> ({t('application.skillTitle')})</span>
      </>}
      extra={
        <Space>
          {/* Help button for skill configuration guidance */}
          <Button style={{ padding: '0 8px', height: '24px' }}>{t('application.skillHelp')}</Button>
          {/* Toggle switch to enable/disable skill functionality */}
          <Form.Item 
            valuePropName="checked"
            name={['skills', 'enabled']}
            noStyle
          >
            <Switch />
          </Form.Item>
        </Space>
      }
    >
      {/* Render skill configuration UI only when enabled */}
      {skillConfig?.enabled && <Flex vertical gap={12}>
        {/* Dynamic skill binding configuration section */}
        <Form.Item noStyle>
          <SkillsItem
            title={t('application.dynamicBindingSkill')}
            parentName={['skills']}
            supportAll={true}
            emptyTitle={t('application.dynamicBindingSkill_empty')}
          />
        </Form.Item>
        {/* Execution process preview card showing workflow steps */}
        <Card
          title={t('application.executeProcessPreview')}
          variant="borderL"
        >
          <Flex align="center" gap={8} className="rb:text-[12px]">
            {/* Render each step in the process flow with numbered badges */}
            {processObj.map((key, index) => (<>
              <Flex align="center" gap={8} className="rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:bg-white rb:p-2!">
                {/* Step number badge */}
                <div className="rb:size-4 rb:rounded-full rb:bg-[#155EEF] rb:text-white rb:flex rb:items-center rb:justify-center rb:font-medium">{index + 1}</div>
                {/* Step label */}
                <span>{t(`application.${key}`)}</span>
              </Flex>
              {/* Arrow separator between steps (except after last step) */}
              {index !== processObj.length - 1 && <div className="rb:text-[#8C9196]">→</div>}
            </>))}
          </Flex>
        </Card>
      </Flex>}
    </Card>
  )
}
export default SkillList