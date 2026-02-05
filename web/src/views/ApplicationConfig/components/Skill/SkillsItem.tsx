/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:43:03 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:43:03 
 */
import { type FC, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Form, Flex, Tooltip, Checkbox } from 'antd'
import { CloseOutlined, CheckCircleFilled } from '@ant-design/icons'

import type {
  SkillConfigForm,
  SkillModalRef,
} from './types'
import Empty from '@/components/Empty'
import SkillListModal from './SkillListModal'
import Card from '../Card'
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
    <Card
      title={title}
      extra={
        <Space>
          {/* "Allow all skills" checkbox - only shown if supportAll is true */}
          {supportAll && <Form.Item name={[...parentName, 'all_skills']} valuePropName="checked" noStyle>
            <Checkbox>{t('application.allSkill')}</Checkbox>
          </Form.Item>}
          {/* Add skill button - disabled when all skills are enabled */}
          <Button disabled={allSkills} style={{ padding: '0 8px', height: '24px' }} onClick={handleAddSkill}>+ {t('application.addSkill')}</Button>
        </Space>
      }
      variant="borderL"
    >
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
                <Space direction="vertical" size={12} className="rb:w-full">
                  {fields.map((field) => {
                    const skill = form.getFieldValue([...parentName, 'skill_ids', field.name])
                    return (
                      /* Individual skill card */
                      <div key={field.key} className="rb:flex rb:items-center rb:justify-between rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
                        <Flex className="rb:flex-1  rb:max-w-[calc(100%-186px)]!">
                          {/* Skill icon or fallback initial */}
                          {skill.icon
                            ? <img src={skill.icon} className="rb:mr-3.25 rb:size-12 rb:rounded-lg" />
                            : <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                              {skill.name?.[0]}
                            </div>
                          }
                          {/* Skill name and description */}
                          <div className="rb:flex-1 rb:max-w-[calc(100%-60px)]">
                            <div className="rb:font-medium rb:wrap-break-word rb:line-clamp-1">{skill.name}</div>
                            <Tooltip title={skill.desciption}>
                              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:-mt-1 rb:wrap-break-word rb:line-clamp-1">{skill.desciption}</div>
                            </Tooltip>
                          </div>
                        </Flex>
                        <Space size={16} align="center">
                          {/* Remove skill button */}
                          <CloseOutlined 
                            className="rb:cursor-pointer rb:text-[#5B6167] hover:rb:text-[#155EEF]" 
                            onClick={() => remove(field.name)}
                          />
                        </Space>
                      </div>
                    )
                  })}
                </Space>
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
    </Card>
  )
}
export default SkillsItem