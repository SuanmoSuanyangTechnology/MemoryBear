/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 16:29:21
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 10:47:30
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
import PromptConfigurationCard from './components/PromptConfigurationCard'
import { useAgent } from './hooks/useAgent'
import ModelStatusTag from '@/components/ModelSelect/ModelStatusTag';

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
        <Col span={12} className="rb:h-full! rb:overflow-hidden!">
          <Form form={form} className="rb:h-full! rb:overflow-hidden!">
            <Flex gap={12} vertical className="rb:h-full! rb:overflow-hidden!">
              <Flex align="center" justify="space-between" className="rb:p-3! rb:bg-white rb:rounded-xl">
                <Button type="primary" ghost onClick={handleModelConfig} className="rb:group">
                  {modelLogo
                    ? <img src={modelLogo} className="rb:size-4 rb:rounded-md" alt={modelLogo} />
                    : defaultModel?.name
                    ? <div className="rb:size-4 rb:bg-[url('@/assets/images/application/model.svg')]"></div> : null}
                  {defaultModel?.name || t('application.chooseModel')}
                  {defaultModel && <ModelStatusTag model={defaultModel} />}
                </Button>
                <Space size={12}>
                  <FeaturesConfig
                    value={values?.features as FeaturesConfigForm}
                    input_modalities={values?.input_modalities || []}
                    refresh={handleSaveFeaturesConfig}
                    chatVariables={chatVariables}
                  />
                  <Button type="primary" onClick={() => handleSave()}>
                    {t('common.save')}
                  </Button>
                </Space>
              </Flex>

              <Flex gap={12} vertical className="rb:h-flex-1! rb:overflow-y-auto!">
                <Form.Item name="default_model_config_id" hidden noStyle></Form.Item>
                <Form.Item name="input_modalities" hidden noStyle></Form.Item>
                <Form.Item name="output_modalities" hidden noStyle></Form.Item>
                <Form.Item name="model_parameters" hidden noStyle></Form.Item>
                <Form.Item name="features" hidden noStyle></Form.Item>
                <PromptConfigurationCard
                  title={t('application.promptConfiguration')}
                  promptLabel={t('application.configuration')}
                  promptDescription={t('application.configurationDesc')}
                  fieldName="system_prompt"
                  variableOptions={chatVariables.map(variable => ({
                    label: variable.display_name,
                    value: `{{${variable.name}}}`,
                  }))}
                  onPromptBlur={updateVariables}
                  onAiPromptClick={handlePrompt}
                  editorClassName="rb:h-50 rb:bg-[#FFFFFF]"
                />

                <Form.Item name="knowledge_retrieval" noStyle>
                  <Knowledge />
                </Form.Item>

                  {/* Memory Configuration */}
                <Card title={t('application.memoryConfiguration')}>
                  <Flex gap={16} vertical className="rb:bg-gray-50 rb:rounded-xl rb:p-3!">
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
