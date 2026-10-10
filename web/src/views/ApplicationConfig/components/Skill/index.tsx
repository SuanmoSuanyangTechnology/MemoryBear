/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:42:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-26 10:18:56
 */
import { Fragment, useEffect, type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Switch, Form, Flex } from 'antd'
import clsx from 'clsx'

import type { SkillConfigForm } from './types'
import RbCard from '@/components/RbCard/Card'
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

const DEFAULT_SKILLS_PATH = ['skills']

interface SkillListProps {
  /**  Current skill configuration values */
  value?: SkillConfigForm;
  /** Callback function when configuration changes */
  onChange?: (config: SkillConfigForm) => void;
  /** Parent form path for the skill configuration. */
  name?: string[];
  /** Whether users may enable all skills at once. */
  supportAll?: boolean;
}

/**
 * Skill Configuration Component
 * 
 * Main component for managing agent skill configuration including:
 * - Enabling/disabling skill functionality
 * - Configuring dynamic skill binding
 * - Displaying skill execution process flow
 * - Managing skill selection and assignment
 */
const SkillList: FC<SkillListProps> = ({
  name = DEFAULT_SKILLS_PATH,
  supportAll = true,
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance()
  const skillConfig = (Form.useWatch(name, form) as SkillConfigForm | undefined) || {}

  /**
   * Effect: Fetch and populate skill details for skills without names
   * Ensures all selected skills have complete information by fetching from API
   */
  useEffect(() => {
    const skillIds = skillConfig?.skill_ids ?? []
    const normalizedSkills = skillIds.map(skill => (
      typeof skill === 'string' ? { id: skill } : skill
    ))
    const skillsWithoutName = normalizedSkills.filter(skill => !(skill as Skill).name)

    if (skillsWithoutName.length === 0) return

    getSkillList({ page: 1, pagesize: 100 })
      .then(res => {
        const response = res as { items: Skill[] }
        const skillMap = new Map(response.items.map(skill => [skill.id, skill]))
        const completedSkills = normalizedSkills.map(skill => ({
          ...skill,
          ...skillMap.get(skill.id),
        }))
        const hasChanges = skillIds.some((skill, index) => (
          typeof skill === 'string'
          || (Boolean(skillMap.get(normalizedSkills[index].id)) && !(normalizedSkills[index] as Skill).name)
        ))

        if (hasChanges) {
          form.setFieldValue([...name, 'skill_ids'], completedSkills)
        }
      })
  }, [form, name, skillConfig?.skill_ids])

  useEffect(() => {
    if (skillConfig?.enabled === false) {
      form.setFields([
        { name: [...name, 'all_skills'], value: false },
        { name: [...name, 'skill_ids'], value: [] }
      ])
    }
  }, [name, skillConfig?.enabled, form])


  return (
    <RbCard
      title={<>
        <div className="rb:font-[MiSans-Bold] rb:font-bold">{t('application.skill')}</div>
        <div className="rb:font-regular! rb:text-[12px] rb:text-gray-600">{t('application.skillTitle')}</div>
      </>}
      extra={
        <Space>
          <Form.Item
            valuePropName="checked"
            name={[...name, 'enabled']}
            noStyle
          >
            <Switch />
          </Form.Item>
        </Space>
      }
      headerType="borderless"
      headerClassName={clsx('rb:py-[16px]! rb:leading-[22px]! rb:font-regular', {
        'rb:h-[76px]! rb:py-[16px]!': !skillConfig?.enabled,
        'rb:h-[68px]! rb:pb-2!': skillConfig?.enabled,
      })}
    >
      {skillConfig?.enabled && (
        <Flex vertical gap={8} className="rb:bg-gray-50 rb:rounded-xl rb:pt-2.5! rb:pb-3! rb:px-3!">
          <div className="rb:text-gray-800 rb:font-medium rb:leading-4.5 rb:px-1">
            {t('application.executeProcessPreview')}
          </div>
          <Flex
            align="center"
            justify="space-between"
            gap={14}
            className="rb:text-[12px] rb:bg-[#FFFFFF]! rb:rounded-lg rb-border rb:py-2.5! rb:pl-4! rb:pr-3.25! rb:mb-2!"
          >
            {processObj.map((key, index) => (
              <Fragment key={index}>
                <Flex align="center" gap={8}>
                  <Flex align="center" justify="center" className="rb:size-4 rb:rounded-full rb:bg-[#171719] rb:text-white rb:font-medium">
                    {index + 1}
                  </Flex>
                  <span className="rb:inline-block rb:max-w-16">{t(`application.${key}`)}</span>
                </Flex>
                {index !== processObj.length - 1 && (
                  <div className="rb:w-10 rb:h-4.5 rb:bg-cover rb:bg-[url('@/assets/images/application/arrow_right.svg')]" />
                )}
              </Fragment>
            ))}
          </Flex>
          <Form.Item noStyle>
            <SkillsItem
              title={t('application.dynamicBindingSkill')}
              parentName={name}
              supportAll={supportAll}
              emptyTitle={t('application.dynamicBindingSkill_empty')}
            />
          </Form.Item>
        </Flex>
      )}
    </RbCard>
  )
}

export default SkillList
