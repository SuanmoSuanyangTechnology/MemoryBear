/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:43:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 15:36:14
 */
import { type FC, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Form, Flex, Tooltip, Checkbox } from 'antd'
import { CheckCircleFilled } from '@ant-design/icons'

import type {
  SkillConfigForm,
  SkillModalRef,
} from './types'
import Empty from '@/components/Empty'
import SkillListModal from './SkillListModal'
import RbAlert from '@/components/RbAlert'
import type { Skill } from '@/views/Skills/types'

/**
 * Props for SkillsItem Component
 */
interface SkillsItemProps {
  /** Title displayed in the card header */
  title: string;
  /** Form field path for nested form structure */
  parentName: string[];
  /** Whether to show "Allow all skills" checkbox option */
  supportAll?: boolean;
  /** Message displayed when no skills are configured */
  emptyTitle: string;
}

/**
 * Skills Item Component
 * 
 * Displays and manages a list of configured skills with the following features:
 * - Add new skills via modal selection
 * - Optional "Allow all skills" toggle
 * - Display skill cards with icons and descriptions
 * - Remove individual skills
 * - Empty state when no skills configured
 * - Alert message when all skills are enabled
 * 
 * @param title - Card header title
 * @param parentName - Form field path array for nested structure
 * @param supportAll - Enable "Allow all skills" checkbox
 * @param emptyTitle - Empty state message
 */
const SkillsItem: FC<SkillsItemProps> = ({
  title,
  parentName,
  supportAll = false,
  emptyTitle
}) => {
  const { t } = useTranslation()
  const skillModalRef = useRef<SkillModalRef>(null)
  const form = Form.useFormInstance()
  const allSkills = Form.useWatch([...parentName, 'all_skills'], form)

  /**
   * Opens the skill selection modal
   */
  const handleAddSkill = () => {
    skillModalRef.current?.handleOpen()
  }

  /**
   * Updates form with newly selected skills
   * Merges new selections with existing skills, avoiding duplicates
   * @param values - Array of newly selected skills
   */
  const refresh = (values: SkillConfigForm['skill_ids']) => {
    const currentSkills = form.getFieldValue([...parentName, 'skill_ids']) || []
    const newSkills = values?.filter(v => !currentSkills?.find((s: Skill) => s.id === (v as Skill).id)) || []
    form.setFieldValue([...parentName, 'skill_ids'], [...currentSkills, ...newSkills])
  }

  /**
   * Effect: Clear skill list when "all skills" is enabled
   */
  useEffect(() => {
    form.setFieldValue([...parentName, 'skill_ids'], [])
  }, [allSkills])

  return (
    <div>
      <Flex align="center" justify="space-between" className="rb:mb-2!">
        <div className="rb:text-[#212332] rb:font-medium rb:leading-4.5 rb:px-1">{title}</div>

        <Space size={16}>
          {/* "Allow all skills" checkbox - only shown if supportAll is true */}
          {supportAll && <Form.Item name={[...parentName, 'all_skills']} valuePropName="checked" noStyle>
            <Checkbox className="rb:text-[12px]!">{t('application.allSkill')}</Checkbox>
          </Form.Item>}
          {/* Add skill button - disabled when all skills are enabled */}
          <Button disabled={allSkills} type="link" className="rb:h-4! rb:p-0! rb:font-medium! rb:text-[12px]! rb:text-[#212332]" onClick={handleAddSkill}>+ {t('application.addSkill')}</Button>
        </Space>
      </Flex>
      {/* Show alert when all skills enabled, otherwise show skill list */}
      {allSkills
        ? <RbAlert color="green" icon={<CheckCircleFilled />}>{t('application.allSkillIntro')}</RbAlert>
        : <>
          <Form.List name={[...parentName, 'skill_ids']}>
            {(fields, { remove }) => (
              fields.length === 0 ? (
                /* Empty state when no skills configured */
                <Empty size={88} subTitle={emptyTitle} />
              ) : (
                /* Render list of configured skills */
                <Flex vertical gap={12}>
                  {fields.map((field) => {
                    const skill = form.getFieldValue([...parentName, 'skill_ids', field.name])
                    return (
                      /* Individual skill card */
                      <Flex key={field.key} align="center" justify="space-between" className="rb:p-3! rb:bg-[#FFFFFF] rb-border rb:rounded-lg">
                        <Flex className="rb:flex-1  rb:max-w-[calc(100%-186px)]!">
                          {/* Skill name and description */}
                          <div className="rb:flex-1 rb:max-w-[calc(100%-60px)]">
                            <div className="rb:font-medium rb:text-[#212332] rb:leading-5 rb:wrap-break-word rb:line-clamp-1">{skill.name}</div>
                            <Tooltip title={skill.description}>
                              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:mt-1 rb:wrap-break-word rb:line-clamp-1">{skill.description}</div>
                            </Tooltip>
                          </div>
                        </Flex>
                        <div
                          className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                          onClick={() => remove(field.name)}
                        ></div>
                      </Flex>
                    )
                  })}
                </Flex>
              )
            )}
          </Form.List>
        </>
      }
      {/* Skill selection modal */}
      <SkillListModal
        ref={skillModalRef}
        selectedList={form.getFieldValue([...parentName, 'skill_ids']) || []}
        refresh={refresh}
      />
    </div>
  )
}
export default SkillsItem