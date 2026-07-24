import { type FC, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, Select, Flex, Button } from 'antd'

import { nodeLibrary } from '../../constant'
import HumanIntervention from './HumanIntervention'
import ListOperator from './ListOperator'
import Trigger from './Trigger'
import HttpRequest from './HttpRequest'
import ToolConfig from './ToolConfig'
import JinjaRender from './JinjaRender'
import CodeExecution from './CodeExecution'
import OutputVariables from './OutputVariables'
import NextStep from './NextStep'
import ConfigField from './ConfigField'
import { getCurrentNodeVariables } from './hooks/useVariableList'
import { useProperties } from './PropertiesContext'

/**
 * Renders the body of the "setting" tab: the node id, the node-type specific
 * configuration (either a dedicated component or the generic config field
 * list), the output variables and the "next step" section.
 */
const SettingBody: FC = () => {
  const { t } = useTranslation()
  const {
    selectedNode,
    graphRef,
    values,
    data,
    variableList,
    configs,
    blankClick,
    nodeClick,
    getFilteredVariableList,
  } = useProperties()

  /**
   * Get current node output variables
   */
  const currentNodeVariables = useMemo(() => {
    if (!selectedNode) return []
    return getCurrentNodeVariables(selectedNode?.getData(), values, variableList)
  }, [selectedNode?.getData(), values])

  const handleSureReplace = () => {
    const { replaceNode } = values;
    const nodeLibraryConfig = [...nodeLibrary]
      .flatMap(category => category.nodes)
      .find(n => n.type === replaceNode)

    if (replaceNode && nodeLibraryConfig) {
      // Preserve existing config values when switching node types
      const currentData = selectedNode?.data || {};
      const currentConfig = currentData.config || {};
      const newConfig = nodeLibraryConfig.config || {};

      // Merge configs: keep existing values for matching keys, add new keys from template
      const mergedConfig: Record<string, any> = {};
      Object.keys(newConfig).forEach(key => {
        if (currentConfig[key] && currentConfig[key].defaultValue !== undefined) {
          // Preserve existing value if it exists
          mergedConfig[key] = {
            ...newConfig[key],
            defaultValue: currentConfig[key].defaultValue
          };
        } else {
          // Use new config template
          mergedConfig[key] = { ...newConfig[key] };
        }
      });

      selectedNode?.setData({
        ...currentData,
        ...nodeLibraryConfig,
        config: mergedConfig
      })
      blankClick()
    }
  }

  const handleAddNode = (e: React.MouseEvent, portId?: string) => {
    const graph = graphRef.current;
    if (!graph) return;

    let sourcePortId = portId;
    if (!sourcePortId) {
      const sourceNodePort = selectedNode.getPorts().find((p: any) => p.group === 'right');
      sourcePortId = sourceNodePort?.id || 'right';
    }

    const tempElement = document.createElement('div');
    tempElement.style.cssText = 'position: fixed; width: 1px; height: 1px; z-index: 9999;';

    tempElement.style.left = `${e.clientX}px`;
    tempElement.style.top = `${e.clientY}px`;

    document.body.appendChild(tempElement);

    const event = new CustomEvent('port:click', {
      detail: {
        node: selectedNode,
        port: sourcePortId,
        element: tempElement,
        edgeInsertion: null,
      },
    });
    window.dispatchEvent(event);
  }

  return (
    <>
      <div className="rb:px-3!">
        <Form.Item name="id" label="ID">
          <Input disabled />
        </Form.Item>
        {selectedNode?.data?.type === 'human-intervention'
          ? <HumanIntervention
              options={getFilteredVariableList(selectedNode?.data?.type)}
              selectedNode={selectedNode}
              graphRef={graphRef}
            />
          : selectedNode?.data?.type === 'list-operator'
          ? <ListOperator
            options={variableList}
            selectedNode={selectedNode}
          />
          : selectedNode?.data?.type === 'unknown'
          ? <>
            <Form.Item name="replaceNode" label={t('workflow.config.unknown.replaceNodeType')}>
              <Select
                options={nodeLibrary.map(category => ({
                  label: t(`workflow.${category.category}`),
                  options: category.nodes.filter(item => !['cycle-start', 'break'].includes(item.type)).map(node => ({
                    label: (
                      <Flex align="center" gap={8} className="rb:flex-1">
                        <div className={`rb:size-3.5 rb:bg-cover ${node.icon}`} />
                        <div className="rb:wrap-break-word rb:line-clamp-1">{t(`workflow.${node.type}`)}</div>
                      </Flex>
                    ),
                    value: node.type
                  }))
                }))}
                placeholder={t('common.pleaseSelect')}
                allowClear
              />
            </Form.Item>
            <Button type="primary" size="small" className="rb:text-[12px]!" onClick={handleSureReplace}>{t('workflow.sureReplace')}</Button>
          </>
          : selectedNode?.data?.type === 'trigger'
            ? <Trigger
              key={data.id || 'trigger'}
            />
            : selectedNode?.data?.type === 'http-request'
            ? <HttpRequest
              options={variableList}
              selectedNode={selectedNode}
              graphRef={graphRef}
            />
            : selectedNode?.data?.type === 'tool'
              ? <ToolConfig options={variableList} />
              : selectedNode?.data?.type === 'jinja-render'
                ? <JinjaRender
                  selectedNode={selectedNode}
                  options={getFilteredVariableList(selectedNode?.data?.type, 'mapping')}
                  templateOptions={getFilteredVariableList(selectedNode?.data?.type, 'template')}
                />
                : selectedNode?.data?.type === 'code'
                  ? <CodeExecution
                    graphRef={graphRef}
                    selectedNode={selectedNode}
                    options={getFilteredVariableList(selectedNode?.data?.type, 'mapping')}
                  />
                  : configs && Object.keys(configs).length > 0 && Object.keys(configs).map((key) => (
                    <ConfigField key={key} configKey={key} />
                  ))
        }

        {currentNodeVariables.length > 0 && !(!values?.group && selectedNode.getData().type === 'var-aggregator') &&
          <OutputVariables variables={currentNodeVariables} />
        }
      </div>
      <NextStep
        selectedNode={selectedNode}
        graphRef={graphRef}
        onAddNode={handleAddNode}
        onNodeClick={nodeClick}
        nodeData={data}
      />
    </>
  )
}

export default SettingBody
