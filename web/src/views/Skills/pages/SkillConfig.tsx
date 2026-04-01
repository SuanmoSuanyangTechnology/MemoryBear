/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:44:08 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 10:56:28
 */
import { type FC, useEffect, useRef, useState } from "react";
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Form, Input, Button, Space, Select, App, Flex } from 'antd'

import Card from '@/views/ApplicationConfig/components/Card'
import AiPromptModal from '@/views/ApplicationConfig/components/AiPromptModal'
import ToolList from '../components/ToolList/ToolList'
import type { AiPromptModalRef } from '@/views/ApplicationConfig/types'
import type { SkillFormData } from '../types'
import { getSkillDetail, createSkill, updateSkill } from '@/api/skill'
import { stringRegExp } from '@/utils/validator';
import PageHeader from '@/components/Layout/PageHeader'

/**
 * Skill Configuration Page Component
 * 
 * Page for creating and editing skills with the following sections:
 * - Manifest: Basic skill information (name, description, keywords)
 * - Prompt Configuration: AI instructions with AI assistant
 * - Tool Configuration: Associated tools for the skill
 * 
 * Features:
 * - Create new skills or edit existing ones
 * - AI-powered prompt generation
 * - Tool selection and management
 * - Form validation
 * - Auto-save functionality
 * 
 * @returns Skill configuration form page
 */
const SkillConfig: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { id } = useParams()
  const { message } = App.useApp()
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm<SkillFormData>();
  const [data, setData] = useState<SkillFormData | null>(null)

  /**
   * Effect: Load skill data if editing existing skill
   */
  useEffect(() => {
    if (id) {
      getConfig()
    } else {
      // Initialize default config for new skill
      form.setFieldsValue({
        config: {
          enabled: false,
          keywords: []
        }
      })
    }
  }, [id])

  /**
   * Fetch skill configuration from API
   */
  const getConfig = () => {
    if (!id) return
    setLoading(true)
    getSkillDetail(id)
      .then(res => {
        form.setFieldsValue(res as SkillFormData)
        setData(res as SkillFormData)
      })
      .finally(() => {
        setLoading(false)
      })
  }
  
  const aiPromptModalRef = useRef<AiPromptModalRef>(null)
  
  /**
   * Open AI prompt generation modal
   */
  const handlePrompt = () => {
    aiPromptModalRef.current?.handleOpen()
  }
  
  /**
   * Update prompt field with AI-generated content
   * @param value - Generated prompt text
   */
  const updatePrompt = (value: string) => {
    form.setFieldValue('prompt', value)
  }
  
  /**
   * Navigate back to skills list
   */
  const handleBack = () => {
    navigate('/skills')
  };

  /**
   * Save skill configuration
   * Validates form and calls create or update API
   */
  const handleSave = () => {
    form.validateFields()
      .then((values) => {
        const { tools, ...rest } = values;
        // Format tools data for API
        const formData = {
          ...rest,
          tools: tools?.map((item) => ({
            tool_id: item.tool_id,
            operation: item.operation
          }))
        }
        setLoading(true)
        // Choose create or update based on whether id exists
        const request = id ? updateSkill(id, formData) : createSkill(formData)
        request
          .then(() => {
            message.success(id ? t('common.saveSuccess') : t('common.createSuccess'))
            handleBack()
          })
          .finally(() => {
            setLoading(false)
          })
      })
  }

  return (
    <Flex vertical className="rb:h-screen!">
      <PageHeader
        title={data?.name}
        extra={
          <Flex gap={12} align="center">
            {/* Save button */}
            <Button type="primary" className="rb:px-2! rb:gap-0.5!" disabled={loading} onClick={handleSave}>{t('skills.save')}</Button>
            <Button
              className="rb:px-2! rb:gap-0.5!"
              icon={<div className="rb:bg-[url('@/assets/images/workflow/return.svg')] rb:size-4 rb:bg-cover"></div>}
              onClick={handleBack}
            >
              {t('common.return')}
            </Button>
          </Flex>
        }
      />
      <div className="rb:w-250 rb:my-3 rb:mx-auto rb:flex-1 rb:overflow-y-auto">
        <Form form={form} layout="vertical">
          <Space size={16} direction="vertical" className="rb:w-full">
            {/* Manifest Section: Basic skill information */}
            <Card title={t('skills.mainfest')}>
              <Form.Item
                name="name"
                label={t('skills.name')}
                rules={[
                  { required: true, message: t('common.inputPlaceholder', { title: t('skills.name') }) },
                  { max: 50 },
                  { pattern: stringRegExp, message: t('common.nameInvalid') },
                ]}
              >
                <Input placeholder={t('common.pleaseEnter')} />
              </Form.Item>
              <Form.Item
                name="description"
                label={t('skills.description')}
                rules={[{ max: 500 }]}
              >
                <Input.TextArea placeholder={t('skills.descriptionPlaceholder')} />
              </Form.Item>
              <Form.Item
                name={['config', 'keywords']}
                label={t('skills.keywords')}
                rules={[{ required: true, message: t('common.inputPlaceholder', { title: t('skills.keywords') }) }]}
              >
                <Select
                  mode="tags"
                  placeholder={t('common.pleaseEnter')}
                />
              </Form.Item>
            </Card>

            {/* Prompt Configuration Section: AI instructions */}
            <Card title={t('skills.promptConfiguration')}
              extra={
                <Button style={{ padding: '0 8px', height: '24px' }} onClick={handlePrompt}>
                  <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/application/aiPrompt.png')] rb:mr-1!" />
                  {t('skills.aiPrompt')}
                </Button>
              }
            >
              <Form.Item
                name="prompt"
                className="rb:mb-0!"
              >
                <Input.TextArea
                  placeholder={t('skills.promptPlaceholder')}
                  styles={{
                    textarea: {
                      minHeight: '200px',
                      borderRadius: '8px'
                    },
                  }}
                />
              </Form.Item>
            </Card>

            {/* Tool Configuration Section */}
            <Form.Item
              name="tools"
              rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('skills.tools') }) }]}
              className="rb:mb-0!"
            >
              <ToolList />
            </Form.Item>

          </Space>
        </Form>
        
        {/* AI Prompt Generation Modal */}
        <AiPromptModal
          ref={aiPromptModalRef}
          refresh={updatePrompt}
          source="skills"
        />
      </div>
    </Flex>
  )
}

export default SkillConfig;
