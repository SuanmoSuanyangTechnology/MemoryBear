/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 16:29:21
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-02 16:40:04
 */
import { forwardRef } from 'react';
import { useTranslation } from 'react-i18next'
import { Row, Col, Space, Form, Button, Flex } from 'antd'

import Chat from './components/Chat'
import RbCard from '@/components/RbCard/Card'
import Card from './components/Card'
import ModelConfigModal from './components/ModelConfigModal'
import type { Config, AgentRef, FeaturesConfigForm } from './types'
import Knowledge from '@/components/Knowledge'
import VariableList from './components/VariableList/VariableList'
import AiPromptModal from './components/AiPromptModal'
import ToolList from './components/ToolList/ToolList'
import SkillList from './components/Skill'
import ActiveMemoryConfig from '@/components/ActiveMemoryConfig'
import ChatVariableConfigModal from './components/ChatVariableConfigModal';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem'
import FeaturesConfig from './components/FeaturesConfig'
import Editor from './components/Editor'
import { useAgent } from './hooks/useAgent'

/**
 * Agent configuration component
 * Manages single agent configuration including prompts, knowledge, memory, variables, and tools
 */
const Agent = forwardRef<AgentRef, { onFeaturesLoad?: (features: FeaturesConfigForm | undefined) => void }>(({ onFeaturesLoad }, ref) => {
  const { t } = useTranslation()
  const {
    form,
    values,
    defaultModel,
    modelLogo,
    chatVariables,
    activeMemoryConfig,
    chatList,
    setChatList,
    modelConfigModalRef,
    aiPromptModalRef,
    chatVariableConfigModalRef,
    handleModelConfig,
    handleClearDebugging,
    handleSave,
    handleAddModel,
    handlePrompt,
    handleOpenVariableConfig,
    handleSaveChatVariable,
    handleSaveFeaturesConfig,
    updatePrompt,
    updateVariables,
    refresh,
  } = useAgent(ref, onFeaturesLoad)

  return (
    <>
      <Row className="rb:h-full!" gutter={12}>
        <Col span={12} className="rb:h-full!">
          <Form form={form}>
            <Flex gap={12} vertical>
              <Flex align="center" justify="space-between" className="rb:p-3! rb:bg-white rb:rounded-xl">
                <Button type="primary" ghost onClick={handleModelConfig} className="rb:group">
                  {modelLogo
                    ? <img src={modelLogo} className="rb:size-4 rb:rounded-md" alt={modelLogo} />
                    : defaultModel?.name
                    ? <div className="rb:size-4 rb:bg-[url('@/assets/images/application/model.svg')]"></div> : null}
                  {defaultModel?.name || t('application.chooseModel')}
                </Button>
                <Space size={12}>
                  <FeaturesConfig
                    value={values?.features as FeaturesConfigForm}
                    capability={values?.capability || []}
                    refresh={handleSaveFeaturesConfig}
                    chatVariables={chatVariables}
                  />
                  <Button type="primary" onClick={() => handleSave()}>
                    {t('common.save')}
                  </Button>
                </Space>
              </Flex>

              <Flex gap={12} vertical className="rb:h-[calc(100vh-156px)]! rb:overflow-y-auto!">
                <Form.Item name="default_model_config_id" hidden noStyle></Form.Item>
                <Form.Item name="capability" hidden noStyle></Form.Item>
                <Form.Item name="model_parameters" hidden noStyle></Form.Item>
                <Form.Item name="features" hidden noStyle></Form.Item>
                <Card
                  title={t('application.promptConfiguration')}
                  extra={
                    <Space
                      size={1}
                      className="rb:px-2 rb:h-5.5 rb:rounded-md rb:cursor-pointer rb:border rb:border-[rgba(21,94,239,0.3)] rb:text-[#155EEF]"
                      onClick={handlePrompt}
                    >
                      <div className="rb:size-5 rb:bg-cover rb:bg-[url('@/assets/images/application/aiPrompt.png')]"></div>
                      <span className="rb:font-[PingFangSC, PingFang_SC]!">{t('application.aiPrompt')}</span>
                    </Space>
                  }
                >
                  <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2">
                    <span className="rb:font-medium">{t('application.configuration')}</span>
                    <span className="rb:font-regular rb:text-[#5B6167]"> ({t('application.configurationDesc')})</span>
                  </div>

                  <Form.Item name="system_prompt" className="rb:mb-0!">
                    <Editor
                      options={chatVariables.map(v => ({ label: v.display_name, value: `{{${v.name}}}` }))}
                      placeholder={t('application.promptPlaceholder')}
                      className="rb:h-50 rb:bg-[#FFFFFF]"
                      onBlur={updateVariables}
                      disabled={false}
                    />
                  </Form.Item>
                </Card>

                <Form.Item name="knowledge_retrieval" noStyle>
                  <Knowledge />
                </Form.Item>

                  {/* Memory Configuration */}
                <Card title={t('application.memoryConfiguration')}>
                  <Flex gap={16} vertical className="rb:bg-[#FAFAFA] rb:rounded-xl rb:p-3!">
                    <SwitchFormItem
                      title={t('application.dialogueHistoricalMemory')}
                      name={['memory', 'enabled']}
                      desc={t('application.dialogueHistoricalMemoryDesc')}
                    />
                    <ActiveMemoryConfig
                      activeMemoryConfig={activeMemoryConfig}
                      variant="outline"
                    />
                  </Flex>
                </Card>

                <Form.Item name="variables" noStyle>
                  <VariableList />
                </Form.Item>

                <Form.Item name="skills" noStyle>
                  <SkillList />
                </Form.Item>

                {/* Tool Configuration */}
                <Form.Item name="tools" noStyle>
                  <ToolList />
                </Form.Item>
              </Flex>
            </Flex>
          </Form>
        </Col>
        <Col span={12} className="rb:h-full! rb:overflow-y-hidden">
          <RbCard
            title={t('application.debuggingAndPreview')}
            extra={
              <Space size={10}>
                <Button type="primary" ghost onClick={handleAddModel}>
                  + {t('application.addModel')}
                </Button>
                <div className="rb:w-8 rb:h-8 rb:cursor-pointer rb:bg-[url('@/assets/images/application/clean.svg')]" onClick={handleClearDebugging}></div>
              </Space>
            }
            headerType="borderless"
            headerClassName="rb:h-[56px]! rb:leading-[22px]!"
            titleClassName="rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:p-4! rb:pt-0! rb:h-[calc(100%-56px)]!"
            className="rb:h-full!"
          >
            <Chat
              data={values as Config}
              chatList={chatList}
              updateChatList={setChatList}
              handleSave={handleSave}
              chatVariables={chatVariables}
              handleEditVariables={handleOpenVariableConfig}
            />
          </RbCard>
        </Col>
      </Row>

      <ModelConfigModal
        data={values}
        ref={modelConfigModalRef}
        refresh={refresh}
      />
      <AiPromptModal
        ref={aiPromptModalRef}
        defaultModel={defaultModel}
        refresh={updatePrompt}
      />
      <ChatVariableConfigModal
        ref={chatVariableConfigModalRef}
        refresh={handleSaveChatVariable}
      />
    </>
  );
});

export default Agent;
