import { useEffect, useRef, type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Form, Flex } from 'antd'

import type { SkillConfigForm, SkillModalRef } from './types'
import Empty from '@/components/Empty'
import Card from '../Card'
import SkillListModal from './SkillListModal'
import SkillRow from './SkillRow'
import { getSkillList } from '@/api/skill'
import { Kind, type Skill } from '@/views/Skills/types'
import { getPluginMembers, groupSkills } from './selection'
import Tag from '@/components/Tag'

const DEFAULT_SKILLS_PATH = ['skills']

interface SkillListProps {
  /** Parent form path for the skill configuration. */
  name?: string[];
}

/**
 * Skill Configuration Component
 * 
 * Main component for managing the skills available to an agent.
 */
const SkillList: FC<SkillListProps> = ({
  name = DEFAULT_SKILLS_PATH,
}) => {
  const { t } = useTranslation()
  const skillModalRef = useRef<SkillModalRef>(null)
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
    getSkillList({ page: 1, pagesize: 100, ids: skillIds.map(item => typeof item === 'string' ? item : item?.id).filter(Boolean).join(',') })
      .then(res => {
        const response = res as { items: Skill[] }
        const skillMap = new Map(response.items.flatMap(skill =>
          (skill.kind === Kind.Plugin ? [skill, ...getPluginMembers(skill)] : [skill])
            .map(item => [item.id, item] as const)
        ))
        const completedSkills = normalizedSkills.map(skill => skillMap.get(skill.id) ?? skill as Skill)
        const hasChanges = skillIds.some((skill, index) => (
          typeof skill === 'string'
          || (Boolean(skillMap.get(normalizedSkills[index].id)) && !(normalizedSkills[index] as Skill).name)
        ))

        if (hasChanges) {
          form.setFieldValue([...name, 'skill_ids'], groupSkills(completedSkills, response.items.filter(skill => skill.kind === Kind.Plugin)))
        }
      })
  }, [form, name, skillConfig?.skill_ids])

  /** Opens the skill selection modal. */
  const handleAddSkill = () => {
    skillModalRef.current?.handleOpen()
  }

  /** Merges newly selected skills into the form without duplicates. */
  const refresh = (values: SkillConfigForm['skill_ids']) => {
    const currentSkills = form.getFieldValue([...name, 'skill_ids']) || []
    form.setFieldValue([...name, 'skill_ids'], groupSkills([...currentSkills, ...(values ?? [])]))
  }

  return (
    <Card 
      title={t('application.skill')}
      extra={
        <Button className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-gray-800" onClick={handleAddSkill}>+ {t('application.addSkill')}</Button>
      }
    >
      <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2 rb:font-medium">
        {t('application.skillTitle')}
      </div>
      <Form.List name={[...name, 'skill_ids']}>
        {(fields, { remove }) => (
          fields.length === 0
            ? (
              <Empty size={88}
                subTitle={t('application.dynamicBindingSkill_empty')}
                className="rb:flex rb:items-center rb:justify-center rb:min-h-44.5 rb:rounded-xl rb:border rb:border-[#D6E0EF]"
              />
            )
            : (
              <Flex vertical gap={12}>
                {fields.map((field) => {
                  const skill = form.getFieldValue([...name, 'skill_ids', field.name])
                  return (
                    <div key={field.key} className="rb:px-3 rb:py-1 rb:bg-[#FFFFFF] rb-border rb:rounded-lg">
                      <SkillRow
                        skill={skill}
                        trailing={
                          <>
                            {skill.is_active === false && <Tag color="default" className="rb:shrink-0">{t('skills.unavailable')}</Tag>}
                            <div onClick={() => remove(field.name)}
                              className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_red.svg')]" />
                          </>
                        }
                        renderMember={(_, memberIndex) => {
                          const member = getPluginMembers(skill)[memberIndex]
                          return (
                            <SkillRow
                              key={member.id || memberIndex} skill={member}
                              trailing={
                                <>
                                  {member.is_active === false && <Tag color="default" className="rb:shrink-0">{t('skills.unavailable')}</Tag>}
                                  <button type="button" aria-label={`${t('common.delete')} ${member.name}`}
                                    onClick={() => {
                                      const current = form.getFieldValue([...name, 'skill_ids', field.name]) as Skill
                                      const members = (current.members ?? []).filter((_, index) => index !== memberIndex)
                                      if (members.length === 0) remove(field.name)
                                      else form.setFieldValue([...name, 'skill_ids', field.name], { ...current, members })
                                    }}
                                    className="rb:size-4.5 rb:shrink-0 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_red.svg')]" />
                                </>
                              }
                            />
                          )
                        }}
                      />
                    </div>
                  )
                })}
              </Flex>
            )
        )}
      </Form.List>
      {/* Skill selection modal */}
      <SkillListModal
        ref={skillModalRef}
        selectedList={form.getFieldValue([...name, 'skill_ids']) || []}
        refresh={refresh}
      />
    </Card>
  )
}

export default SkillList
