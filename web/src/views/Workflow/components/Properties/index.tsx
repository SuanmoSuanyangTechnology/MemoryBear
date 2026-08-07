/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 15:39:59
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-15 11:05:08
 */
import { type FC, useEffect, useState } from "react";
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { Graph, Node } from '@antv/x6';
import { Form, Input, Flex, Space, Dropdown, type MenuProps, App, Popover, Tabs } from 'antd';

import type { NodeConfig, ChatVariable, EnvVariable } from '../../types'
import { useVariableList } from './hooks/useVariableList'
import { useFilteredVariableList } from './hooks/useFilteredVariableList'
import { useWorkflowStore } from '@/store/workflow'
import styles from './properties.module.css'
import { cannotRunNodes } from '../../constant';
import RbCard from '@/components/RbCard/Card';
import type { Model } from '@/views/ModelManagement/types';
import SingleNodeRun from '../SingleNodeRun'
import RunResultDisplay, { type RunResult } from '../SingleNodeRun/RunResultDisplay'
import type { Application } from '@/views/ApplicationManagement/types'
import { getWorkflowNodeLastRunDetail } from '@/api/application'
import { openHelpCenter } from '@/utils/help';
import type { Memory } from '@/views/MemoryManagement/types'
import { PropertiesProvider, type PropertiesContextValue } from './PropertiesContext'
import SettingBody from './SettingBody'

/**
 * Props for Properties component
 */
interface PropertiesProps {
  /** Currently selected node */
  selectedNode: Node;
  /** Reference to graph instance */
  graphRef: React.MutableRefObject<Graph | undefined>;
  /** Handler for blank canvas click */
  blankClick: () => void;
  /** Handler for delete event */
  deleteEvent: () => void;
  /** Handler for copy event */
  copyEvent: () => void;
  /** Handler for paste event */
  parseEvent: () => void;
  /** Workflow configuration */
  config?: any;
  /** App ID for node run */
  appId?: string;
  /** Chat variables */
  chatVariables: ChatVariable[];
  /** Environment variables */
  envVariables: EnvVariable[];
  /** Function to save workflow configuration */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Handler for node click */
  nodeClick: ({ node }: { node: Node }) => void;
  /** Application type */
  appType?: Application['type'];
  /** Function to refresh cache */
  refreshCache: () => void;
  activeMemoryConfig?: Memory | null;
}

/**
 * Properties panel component
 * Displays and manages configuration for selected workflow node
 * @param props - Component props
 */
const Properties: FC<PropertiesProps> = ({
  selectedNode,
  graphRef,
  chatVariables,
  envVariables,
  blankClick,
  config,
  appId,
  handleSave,
  nodeClick,
  appType,
  refreshCache,
  activeMemoryConfig,
}) => {
  const { t, i18n } = useTranslation()
  const { message } = App.useApp()
  const { getCheckResults } = useWorkflowStore()
  const [form] = Form.useForm<NodeConfig>();
  const [configs, setConfigs] = useState<Record<string, NodeConfig>>({} as Record<string, NodeConfig>)
  const values = Form.useWatch([], form);
  const variableList = useVariableList(selectedNode, graphRef, chatVariables, envVariables, appType)
  const getFilteredVariableList = useFilteredVariableList(selectedNode, graphRef, variableList)
  const data = selectedNode.getData() || {}

  const [advancedSettingsCollapsed, setAdvancedSettingsCollapsed] = useState(false)
  const [modelOptions, setModelOptions] = useState<Model[]>([])
  const [isRun, setIsRun] = useState(false);
  const [nameHover, setNameHover] = useState(false)
  const [activeKey, setActiveKey] = useState('setting')
  const [result, setResult] = useState<RunResult>({} as RunResult)
  const [resultLoading, setResultLoading] = useState(false)

  useEffect(() => {
    if (selectedNode?.getData()?.id) {
      setAdvancedSettingsCollapsed(false)
      setActiveKey('setting');
    }
    form.resetFields()
  }, [selectedNode?.getData()?.id])

  useEffect(() => {
    if (selectedNode && form) {
      const { type = 'default', name = '', config, id } = selectedNode.getData() || {}
      const initialValue: Record<string, any> = {}
      Object.keys(config || {}).forEach(key => {
        if (config && config[key] && 'defaultValue' in config[key]) {
          initialValue[key] = config[key].defaultValue
        }
      })

      form.setFieldsValue({
        type,
        id,
        name,
        ...initialValue,
      })
      setConfigs(config || {})
    } else {
      form.resetFields()
    }
  }, [selectedNode, form])

  useEffect(() => {
    if (values && selectedNode) {
      const nodeData = selectedNode.getData()
      const { id, knowledge_retrieval, group, group_variables, ...rest } = values
      const { knowledge_bases = [], name: _name, description: _description, ...restKnowledgeConfig } = (knowledge_retrieval as any) || {}

      let allRest = {
        ...rest,
        ...restKnowledgeConfig,
      }
      if (knowledge_bases?.length) {
        allRest.knowledge_bases = knowledge_bases?.map((vo: any) => ({
          id: vo.id,
          ...vo.config
        }))
      } else if (nodeData.type === 'knowledge-retrieval') {
        allRest.knowledge_bases = []
      }


      Object.keys(values).forEach(key => {
        if (nodeData?.config?.[key]) {
          // Create a deep copy to avoid reference sharing between nodes
          if (!nodeData.config[key]) {
            nodeData.config[key] = {};
          }
          nodeData.config[key] = {
            ...nodeData.config[key],
            defaultValue: values[key]
          };
        }
      })

      selectedNode?.setData({
        ...nodeData,
        ...allRest,
      }, { deep: false })
    }
  }, [values, selectedNode, form])

  const handleClick: MenuProps['onClick'] = (e) => {
    switch (e.key) {
      case 'delete':
        selectedNode.remove()
        break;
      case 'copy':
        break;
    }
  }

  const handleRun = () => {
    handleSave?.(false)
      .then(() => {
        if (appId) {
          const nodeResult = getCheckResults(appId).find(r => r.id === selectedNode.id)
          const configErrors = nodeResult?.errors.filter((e: any) => e.key !== 'notConnected') ?? []
          if (configErrors.length) {
            message.error(configErrors[0].message)
            return
          }
        }
        setIsRun(true)
      })
  }

  const getNodeLastRun = () => {
    if (!appId || !data?.id) return
    setResultLoading(true)
    getWorkflowNodeLastRunDetail(appId, data?.id || '')
      .then(res => {
        setResult(res as RunResult)
      })
      .finally(() => {
        setResultLoading(false)
      })
  }

  const gotoHelpCenter = () => {
    const currentLang = i18n.language;
    const lang = currentLang === 'zh' ? 'zh' : 'en';
    openHelpCenter(lang, data.type);
  };

  useEffect(() => {
    if (!isRun) {
      getNodeLastRun()
    }
  }, [isRun])

  useEffect(() => {
    if (activeKey === 'setting') {
      setResult({} as RunResult)
      setResultLoading(false)
    } else {
      getNodeLastRun()
    }
  }, [activeKey])

  const contextValue: PropertiesContextValue = {
    selectedNode,
    graphRef,
    form,
    values: values as NodeConfig,
    data,
    variableList,
    configs,
    appType,
    activeMemoryConfig,
    modelOptions,
    setModelOptions,
    advancedSettingsCollapsed,
    setAdvancedSettingsCollapsed,
    getFilteredVariableList,
    blankClick,
    nodeClick,
  }

  return (
    <div className={clsx("rb:w-90 rb:absolute rb:right-2.5 rb:top-14 rb:bottom-2.5 rb:z-1000", styles.properties)}>
      <Form key={selectedNode?.getData()?.id} form={form} size="small" layout="vertical" className="rb:h-full!">
        <RbCard
          title={() => (
            <Flex gap={4} align="center">
              <div className={clsx("rb:size-6 rb:bg-cover rb:shrink-0", data.icon)}></div>
              <Form.Item name="name" noStyle>
                <Input
                  placeholder={t('common.pleaseEnter')}
                  variant="underlined"
                  size="large"
                  onFocus={() => setNameHover(true)}
                  onBlur={() => setNameHover(false)}
                  className={clsx('rb:px-1! rb:py-0!', {
                    'rb:border-b-[#FFFFFF]!': !nameHover,
                    'rb:border-b-[#EBEBEB]!': nameHover
                  })}
                />
              </Form.Item>
            </Flex>
          )}
          headerType="borderless"
          headerClassName={clsx("rb:font-[MiSans-Bold] rb:font-bold rb:min-h-[48px]!")}
          className="rb:h-full! rb:hover:shadow-none!"
          bodyClassName={clsx('rb:overflow-y-auto! rb:h-[calc(100%-48px)]! rb:p-0! rb:pb-3!')}
          extra={<Space>
            {['memory-read', 'memory-write'].includes(data?.type) &&
              <Popover content={t('quickActions.helpCenter')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
                <div
                  className="rb:cursor-pointer rb:size-4 rb:hover:bg-[#F6F6F6] rb:rounded-sm rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"
                  onClick={gotoHelpCenter}
                ></div>
              </Popover>
            }
            {!cannotRunNodes.includes(selectedNode?.data?.type) && <Popover content={t('workflow.singleRun')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-4 rb:hover:bg-[#F6F6F6] rb:rounded-sm rb:bg-cover rb:bg-[url('@/assets/images/workflow/run.svg')]"
                onClick={handleRun}
              ></div>
            </Popover>}
            <Dropdown
              menu={{
                items: [
                  { key: 'delete', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_dark.svg')]"></div>, label: <Flex>{t('common.delete')}</Flex> },
                  // { key: 'copy', icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/copy_dark.svg')]"></div>, label: t('common.copy') }
                ],
                onClick: handleClick
              }}
            >
              <div className="rb:cursor-pointer rb:size-4 rb:hover:bg-[#F6F6F6] rb:rounded-sm rb:bg-cover rb:bg-[url(@/assets/images/common/dash.svg)]">
              </div>
            </Dropdown>
            <div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')]" onClick={blankClick}></div>
          </Space>}
        >
          <Tabs
            items={[
              { key: 'setting', label: t('workflow.config.setting') },
              { key: 'lastRun', label: t('workflow.config.lastRun') },
            ]}
            activeKey={activeKey}
            onChange={setActiveKey}
            size="small"
            className={styles.tabs}
          />
          <div className="rb:h-[calc(100%-54px)] rb:overflow-y-auto!">
            {activeKey === 'setting' &&
              <PropertiesProvider value={contextValue}>
                <SettingBody />
              </PropertiesProvider>
            }
            {activeKey === 'lastRun' &&
              <div className="rb:px-3!">
                <RunResultDisplay
                  result={result}
                  loading={resultLoading}
                  nodeData={data}
                />
              </div>
            }
          </div>
        </RbCard>
      </Form>

      {isRun && (
        <SingleNodeRun
          open={isRun}
          onClose={() => setIsRun(false)}
          selectedNode={selectedNode}
          appId={appId || config?.app_id || ''}
          variableList={variableList}
          refreshCache={refreshCache}
        />
      )}
    </div>
  );
};
export default Properties;
