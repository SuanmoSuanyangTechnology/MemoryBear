import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next'
import { Flex } from 'antd';
import { useNavigate } from 'react-router-dom';

import type { GraphRef } from '../types'
import type { Application } from '@/views/ApplicationManagement/types'
import type { WorkflowRef } from '@/views/ApplicationConfig/types'
import IconButtonGroup from '@/components/IconButton/IconButtonGroup'
import CheckListDrawer, { type CheckListDrawerRef } from './CheckList/CheckListDrawer'
import SystemVariableModal, { type SystemVariableModalRef } from './SystemVariableModal'
import { useWorkflowCheck } from '../hooks/useWorkflowCheck'
interface OperationsProps {
  graphRef: GraphRef;
  /** Application type, used to decide which buttons to render. */
  appType?: Application['type'];
  /** Workflow ref, used by CheckList. */
  workflowRef: React.RefObject<WorkflowRef>;
  /** Application id, used by CheckList. */
  appId?: string;
  /** Open features config modal. */
  onFeaturesConfig: () => void;
  /** Clear the canvas. */
  onClear: () => void;
  /** Open add-chat-variable modal. */
  onAddVariable: () => void;
  /** Open add-env-variable modal. */
  onAddEnvVariable: () => void;
  /** Run the workflow. */
  onRun: () => void;
  /** Save the workflow. */
  onSave: () => void;
}
export interface OperationsRef {
  setIsVariableInspectorVisible: (visible: boolean) => void;
}

const Operations: FC<OperationsProps> = ({
  graphRef,
  appType,
  workflowRef,
  appId,
  onFeaturesConfig,
  onClear,
  onAddVariable,
  onAddEnvVariable,
  onRun,
  onSave,
}) => {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const systemVariableModalRef = useRef<SystemVariableModalRef>(null)
  const checkListDrawerRef = useRef<CheckListDrawerRef>(null)

  const { results, errorCount } = useWorkflowCheck(graphRef, appId)

  /**
   * Navigate to application list
   */
  const goToApplication = () => {
    navigate('/application', { replace: true })
  }

  return (
    <Flex
      gap={8}
      className="rb:absolute rb:top-3 rb:right-2.5 rb:z-1000 rb:rounded-lg rb:overflow-hidden"
    >
      <IconButtonGroup
        iconClassName="rb:size-8!"
        className="rb:bg-white rb:border-none! rb:shadow-[0px_1px_4px_0px_rgba(23,23,25,0.1)]"
        items={[
          {
            title: t('workflow.checkList'),
            icon: "rb:bg-[url('@/assets/images/workflow/checkList.svg')]",
            badge: errorCount,
            onClick: () => checkListDrawerRef.current?.handleOpen()
          },
          ...(appType === 'workflow' ? [{
            title: t('application.features'),
            icon: "rb:bg-[url('@/assets/images/workflow/features.svg')]",
            onClick: onFeaturesConfig,
          }] : []),
          {
            title: t('workflow.clear'),
            icon: "rb:bg-[url('@/assets/images/workflow/clear.svg')]",
            onClick: onClear,
          },
        ]}
      />
      <IconButtonGroup
        iconClassName="rb:size-8!"
        className="rb:bg-white rb:border-none! rb:shadow-[0px_1px_4px_0px_rgba(23,23,25,0.1)]"
        items={[
          ...(appType === 'workflow' ? [{
            title: t('workflow.addVariable'),
            icon: "rb:bg-[url('@/assets/images/workflow/variable.svg')]",
            onClick: onAddVariable,
          }] : []),
          {
            title: t('workflow.systemVariable'),
            icon: "rb:bg-[url('@/assets/images/workflow/system.svg')]",
            onClick: () => systemVariableModalRef.current?.handleOpen(),
          },
          {
            title: t('workflow.addEnvVariable'),
            icon: "rb:bg-[url('@/assets/images/workflow/env.svg')]",
            onClick: onAddEnvVariable,
          },
        ]}
      />
      <IconButtonGroup
        iconClassName="rb:size-8!"
        className="rb:bg-white rb:border-none! rb:shadow-[0px_1px_4px_0px_rgba(23,23,25,0.1)]"
        items={[
          {
            title: t('workflow.run'),
            icon: "rb:bg-[url('@/assets/images/workflow/run_dark.svg')]",
            onClick: onRun,
          },
          {
            title: t('workflow.save'),
            icon: "rb:bg-[url('@/assets/images/workflow/save.svg')]",
            onClick: onSave,
          },
          {
            title: t('common.return'),
            icon: "rb:bg-[url('@/assets/images/workflow/return.svg')]",
            onClick: goToApplication,
          },
        ]}
      />
      <CheckListDrawer
        ref={checkListDrawerRef}
        results={results}
        workflowRef={workflowRef}
      />
      <SystemVariableModal ref={systemVariableModalRef} appType={appType} />
    </Flex>
  );
};

export default Operations;
