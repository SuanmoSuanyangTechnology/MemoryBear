/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-04 14:56:04 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 20:08:55
 */
import React from 'react'
import { Flex } from 'antd'
import clsx from 'clsx'
import RenderedForm from '@/components/Chat/RenderedForm'
import type { Intervention } from './types'

interface InterventionItemProps {
  intervention: Intervention
  messageIndex: number;
  index: number;
  isExpanded: boolean
  onToggle: (messageIndex: number, interventionIndex: number) => void
  onActionClick: (actionId: string, fieldValues: Record<string, string>, executionId?: string, nodeId?: string) => void;
  editable: boolean;
}

const InterventionItem: React.FC<InterventionItemProps> = ({
  intervention,
  messageIndex,
  index,
  isExpanded,
  onToggle,
  onActionClick,
  editable
}) => {
  const isEditable = !intervention?.resolved_action_id && editable
  // 不可编辑状态时默认收起，使用外部传入的展开状态
  const shouldExpand = isEditable || isExpanded

  return (
    <div
      key={index}
      className={clsx("rb:mb-4 rb-border rb:rounded-xl rb:px-4 rb:pt-4 rb:bg-white", {
        'rb:hover:bg-[#F6F6F6] rb:w-64': !shouldExpand,
        'rb:pb-4': !isEditable,
        'rb:pb-2': isEditable
      })}
    >
      <Flex
        align="center"
        justify="space-between"
        className="rb:font-medium"
      >
        <span>{intervention.node_name || ''}</span>
        <Flex
          align="center"
          justify="center"
          className={clsx("rb:size-6.5 rb:cursor-pointer rb-border rb:rounded-lg", {
            'rb:hover:bg-[#F6F6F6]!': shouldExpand
          })}
          onClick={() => onToggle(messageIndex, index)}
        >
          <div
            className={clsx("rb:size-4 rb:bg-cover", {
              'rb:bg-[url("@/assets/images/conversation/compress.svg")]': shouldExpand,
              'rb:bg-[url("@/assets/images/conversation/expand.svg")]': !shouldExpand
            })}
          ></div>
        </Flex>
      </Flex>
      {shouldExpand &&
        <div className="rb:mt-2">
          <RenderedForm
            key={intervention.node_id || index}
            content={intervention.rendered_content || ''}
            formFields={isEditable ? intervention.form_fields || [] : Object.entries(intervention.resolved_form_data || {}).map(([id, value]) => ({ id, default_value: value }))}
            actions={intervention.actions || []}
            resolved_action_id={intervention.resolved_action_id || ''}
            timeout_at={intervention.timeout_at}
            onActionClick={(actionId, fieldValues) => onActionClick(actionId, fieldValues, intervention.execution_id, intervention.node_id)}
            editable={isEditable}
          />
        </div>
      }
    </div>
  )
}

interface InterventionListProps {
  interventions: Intervention[]
  messageIndex: number;
  isExpanded: (messageIndex: number, interventionIndex: number, resolved_action_id?: string) => boolean
  toggle: (messageIndex: number, interventionIndex: number) => void
  onActionClick: (actionId: string, fieldValues: Record<string, string>, executionId?: string, nodeId?: string) => void;
  isEdit: boolean;
}

const InterventionList: React.FC<InterventionListProps> = ({
  interventions,
  messageIndex,
  isExpanded,
  toggle,
  onActionClick,
  isEdit
}) => {
  return interventions.map((intervention, idx: number) => (
    <InterventionItem
      key={idx}
      intervention={intervention}
      messageIndex={messageIndex}
      index={idx}
      isExpanded={isExpanded(messageIndex, idx, intervention.resolved_action_id)}
      onToggle={toggle}
      onActionClick={onActionClick}
      editable={isEdit}
    />
  ))
}

export default InterventionList
