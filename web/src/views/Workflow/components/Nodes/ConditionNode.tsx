import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx';
import type { ReactShapeConfig } from '@antv/x6-react-shape';
import { Flex } from 'antd';

import NodeTools from './NodeTools'
import { useVariableList } from '../Properties/hooks/useVariableList'
import { isSubExprSet } from '../../utils'
import { fileSubFieldOperators } from '../Properties/CaseList'

const calculateIsSet = (item: any, type: string) => {
  switch (type) {
    case 'categories':
      return typeof item?.class_name === 'string' && item?.class_name !== ''
    case 'cases': {
      if (item?.sub_variable_condition !== undefined) {
        return !!item.left && !!item.operator
      }
      if (!item.left) return false
      if (['not_empty', 'empty'].includes(item.operator)) return true
      return !!item.left && (!!item.right || typeof item.right === 'boolean' || typeof item.right === 'number')
    }
  }
}
const ConditionNode: ReactShapeConfig['component'] = ({ node }) => {
  const data = node?.getData() || {};
  const { t } = useTranslation()
  const graphRef = useRef(node?.model?.graph)
  const variableList = useVariableList(node ?? null, graphRef, data.chatVariables ?? [])

  const getLocaleField = (field: string, filedType: string) => {
    const key = filedType === 'boolean'
      ? `workflow.config.if-else..boolean.${field}`
      : filedType === 'number'
      ? `workflow.config.if-else.num.${field}`
      : `workflow.config.if-else.${field}`
    const value = t(key)
    return value !== key ? value : t(`workflow.config.if-else.num.${field}`)
  };
  const getSubLocaleField = (field: string, fieldKey: string) => {
    const operators = fileSubFieldOperators[fieldKey] ?? fileSubFieldOperators.default
    const match = operators?.find(op => op.value === field)
    return match?.label ? t(match.label as string) : field
  }

  const labelRender = (value: string) => {
    const filterOption = variableList.find(vo => `{{${vo.value}}}` === value)
      ?? variableList.flatMap(vo => vo.children ?? []).find(child => `{{${child.value}}}` === value)
      ?? variableList.flatMap(vo => vo.children ?? []).flatMap((child: any) => child.children ?? []).find((grandchild: any) => `{{${grandchild.value}}}` === value)

    if (filterOption) {
      return (
        <span
          className="rb:max-w-[40%] rb:break-all rb:line-clamp-1 rb:text-[#155EEF]"
          contentEditable={false}
        >
          {`{x}`} {filterOption.label}
        </span>
      )
    }
    return null
  }

  return (
    <div className={clsx('rb:cursor-pointer rb:group rb:relative rb:h-full rb:w-full rb:p-3 rb:border rb:rounded-2xl rb:bg-[#FCFCFD] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)]', {
      'rb:border-[#171719]': data.isSelected,
      'rb:border-[#FCFCFD]': !data.isSelected
    })}>
      <NodeTools node={node} />
      <Flex align="center" gap={8} className="rb:flex-1">
        <div className={`rb:size-6 rb:bg-cover ${data.icon}`} />
        <div className="rb:wrap-break-word rb:line-clamp-1">{data.name ?? t(`workflow.${data.type}`)}</div>
      </Flex>

      {data.type === 'question-classifier' &&
        <Flex vertical gap={4} className="rb:mt-3!">
          {data.config?.categories?.defaultValue.map((item: any, index: number) => (
            <div key={index} className="rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:py-1 rb:px-1.5 rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-3.5">
              <Flex justify="space-between">
                <span>{t('workflow.config.question-classifier.class_name')} {index + 1}</span>
                {calculateIsSet(item, 'categories') ? t(`workflow.config.${data.type}.set`) : t(`workflow.config.${data.type}.unset`)}
              </Flex>
            </div>
          ))}
        </Flex>
      }
      {data.type === 'if-else' &&
        <Flex vertical gap={4} className="rb:mt-3!">
          {data.config?.cases?.defaultValue.map((item: any, index: number) => (
            <div key={index} className={item.expressions.length > 0 ? '' : 'rb:mb-1'}>
              <Flex justify={item.expressions.length > 0 ? "space-between" : 'end'} className="rb:mb-1! rb:leading-4">
                {item.expressions.length > 0 && <span className="rb:text-[#5B6167] rb:text-[10px] rb:pl-1">CASE{index + 1}</span>}
                <span className="rb:text-[#212332] rb:font-medium rb:text-[12px]">{index === 0 ? 'IF' : `ELIF`}</span>
              </Flex>
              {item.expressions.length > 0 && <Flex vertical gap={2}>
                {item.expressions.map((expression: any, eIndex: number) => (
                  <div key={eIndex} className="rb:relative">
                    {item.expressions.length > 1 && eIndex > 0 &&
                      <div className="rb:absolute rb:-top-2 rb:right-2 rb:text-[10px] rb:text-[#155EEF] rb:font-medium rb:leading-3.5 rb:text-right rb:pr-0.5">{item.logical_operator?.toLocaleUpperCase()}</div>
                    }
                    <Flex vertical gap={2}
                      className={clsx("rb:bg-[#F0F3F8] rb:shadow-[0px_2px_4px_0px_rgba(23,23,25,0.03)] rb:rounded-md rb:px-1.5! rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-4", {
                        'rb:pt-1!': expression.sub_variable_condition?.conditions?.length > 0,
                        'rb:py-1!': !expression.sub_variable_condition?.conditions || !expression.sub_variable_condition?.conditions?.length
                      })}
                    >
                      <Flex align="center">
                      {calculateIsSet(expression, 'cases')
                        ? <>
                          {labelRender(expression.left)}
                          <span className="rb:mx-1">{getLocaleField(expression.operator, typeof expression.right)}</span>
                          <span className="rb:break-all rb:line-clamp-1">{!['not_empty', 'empty'].includes(expression.operator) && <span>{typeof expression.right === 'boolean' ? String(expression.right).charAt(0).toUpperCase() + String(expression.right).slice(1) : expression.right}</span>}</span>
                        </>
                        : t(`workflow.config.${data.type}.unset`)
                      }
                      </Flex>
                      {expression.sub_variable_condition?.conditions?.length > 0 && expression.sub_variable_condition?.conditions.every(isSubExprSet)
                        ? <div className="rb-border-l rb:ml-2 rb:mt-1">
                          {expression.sub_variable_condition?.conditions.map((sub: any, sIndex: number) => (
                            <div key={sIndex} className="rb:relative">
                              {expression.sub_variable_condition?.conditions.length > 1 && sIndex > 0 && <div className="rb:absolute rb:-top-2 rb:right-2 rb:text-[10px] rb:text-[#155EEF] rb:font-medium rb:leading-3.5 rb:text-right rb:pr-0.5">{expression.sub_variable_condition?.logical_operator?.toLocaleUpperCase()}</div>}
                              <Flex align="center"
                                className={clsx("rb:px-1.5! rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-3.5", {
                                  'rb:py-1!': sIndex !== 0,
                                  'rb:pb-1': sIndex === 0
                                })}
                              >
                                <span className="rb:text-[#155EEF]">{sub.key}</span>
                                <span className="rb:mx-1">{getSubLocaleField(sub.operator, sub.key)}</span>
                                <span className="rb:break-all rb:line-clamp-1">
                                  {sub.key === 'type'
                                    ? t(`application.${sub.value}`)
                                    :!['not_empty', 'empty'].includes(sub.operator)
                                    ? <span>{typeof sub.value === 'boolean' ? String(sub.value).charAt(0).toUpperCase() + String(sub.value).slice(1) : sub.value}</span>
                                    : null
                                  }
                                </span>
                              </Flex>
                            </div>
                          ))}
                        </div>
                        : expression.sub_variable_condition?.conditions?.length > 0
                        ? <Flex align="center" className="rb:pl-2! rb:rounded-md rb:pb-1! rb:px-1.5! rb:text-[10px] rb:text-[#5B6167] rb:font-medium rb:leading-4">
                          {t(`workflow.config.${data.type}.unset`)}
                        </Flex>
                        : null
                      }
                    </Flex>
                  </div>
                ))}
              </Flex>}
            </div>
          ))}
          <Flex justify="end" className="rb:text-[#212332] rb:font-medium rb:text-[12px]">
            ELSE
          </Flex>
        </Flex>
      }
    </div>
  );
};

export default ConditionNode;