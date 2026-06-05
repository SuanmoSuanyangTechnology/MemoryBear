/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-04 12:30:24
 */
import { type FC, useRef, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Tabs, Dropdown, Flex, Popover } from 'antd';
import type { MenuProps } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import styles from '../index.module.css'
import type { Application, ApplicationModalRef, UploadWorkflowModalRef } from '@/views/ApplicationManagement/types';
import ApplicationModal from '@/views/ApplicationManagement/components/ApplicationModal'
import type { CopyModalRef, AgentRef, ClusterRef, WorkflowRef, FeaturesConfigForm } from '../types'
import { deleteApplication, appExport } from '@/api/application'
import CopyModal from './CopyModal'
import PageHeader from '@/components/Layout/PageHeader'
import CheckList from '@/views/Workflow/components/CheckList'
import UploadModal from '@/views/ApplicationManagement/components/UploadModal'
import SystemVariableModal, { type SystemVariableModalRef } from '@/views/Workflow/components/SystemVariableModal'

/**
 * Tab keys for application configuration
 */
const tabKeys = ['arrangement', 'api', 'release', 'log', 'statistics']
const sharingTabKeys = [
  'test',
  'log',
  'api'
]

/**
 * Menu icon mapping
 */
const menuIcons: Record<string, string> = {
  edit: "rb:bg-[url('@/assets/images/common/edit_bold.svg')]",
  copy: "rb:bg-[url('@/assets/images/copy_hover.svg')]",
  export: "rb:bg-[url('@/assets/images/application/export.svg')]",
  uploadCover: "rb:bg-[url('@/assets/images/application/import.svg')]",
  delete: "rb:bg-[url('@/assets/images/common/delete_red_big.svg')]"
}

/**
 * Props for ConfigHeader component
 */
interface ConfigHeaderProps {
  /** Application data */
  application?: Application;
  /** Active tab key */
  activeTab: string;
  /** Tab change handler */
  handleChangeTab: (key: string) => void;
  /** Refresh application data */
  refresh: () => void;
  /** Workflow component ref */
  workflowRef: React.RefObject<WorkflowRef>
  /** App component ref (Agent/Cluster/Workflow) */
  appRef?: React.RefObject<AgentRef | ClusterRef | WorkflowRef>
  /** Features config from parent state */
  features?: FeaturesConfigForm;
}

/**
 * Configuration header component
 * Displays application name, tabs, and action buttons
 */
const ConfigHeader: FC<ConfigHeaderProps> = ({
  application, activeTab, handleChangeTab, refresh,
  workflowRef,
  appRef,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id, source } = useParams();
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const copyModalRef = useRef<CopyModalRef>(null);
  const uploadModalRef = useRef<UploadWorkflowModalRef>(null);
  const systemVariableModalRef = useRef<SystemVariableModalRef>(null);

  /**
   * Format tab items for display
   */
  const formatTabItems = useMemo(() => {
    const isHasAnnotations = application?.type !== 'multi_agent' && source !== 'sharing'
    return (source === 'sharing' ? sharingTabKeys : tabKeys).map(key => ({
      key,
      label: key === 'log' && isHasAnnotations ? t('application.logAnnotations') : t(`application.${key}`),
    }))
  }, [source, sharingTabKeys, tabKeys, application?.type])
  /**
   * Handle menu item click
   */
  const handleClick: MenuProps['onClick'] = ({ key }) => {
    if (!application) return
    switch (key) {
      case 'edit':
        applicationModalRef.current?.handleOpen(application)
        break;
      case 'copy':
        appRef?.current?.handleSave(false)
          .then(() => {
            copyModalRef.current?.handleOpen()
          })
        break;
      case 'export':
        appRef?.current?.handleSave(false)
          .then(() => {
            appExport(application.id, application.name)
          })
        break;
      case 'delete':
        handleDelete()
        break;
      case 'uploadCover':
        uploadModalRef.current?.handleOpen()
        break
    }
  }
  /**
   * Delete application with confirmation
   */
  const handleDelete = () => {
    if (!id) {
      return
    }
    deleteApplication(id as string)
      .then(() => {
        goToApplication()
      })
      .catch(() => {
        console.error('Failed to delete application');
      });
  }
  /**
   * Navigate to application list
   */
  const goToApplication = () => {
    navigate('/application', { replace: true })
  }
  /**
   * Save workflow configuration
   */
  const save = () => {
    workflowRef.current?.handleSave()
  }
  /**
   * Run workflow
   */
  const run = () => {
    workflowRef.current?.handleSave(false)
      .then(() => {
        workflowRef.current?.handleRun()
      })
  }
  /**
   * Clear workflow canvas
   */
  const clear = () => {
    workflowRef?.current?.graphRef?.current?.clearCells()
  }
  /**
   * Add variable to workflow
   */
  const addVariable = () => {
    workflowRef?.current?.addVariable()
  }
  /**
   * Add environment variable to workflow
   */
  const addEnvVariable = () => {
    workflowRef?.current?.addEnvVariable()
  }
  /**
   * Format dropdown menu items
   */
  const formatMenuItems = useMemo(() => {
    const items = (application?.type !== 'multi_agent' ? ['edit', 'copy', 'export', 'uploadCover', 'delete'] : ['edit', 'copy', 'delete']).map(key => ({
      key,
      icon: <div className={`rb:size-4 rb:mr-2 ${menuIcons[key]}`} />,
      danger: key === 'delete',
      label: key === 'uploadCover' ? t('application.uploadCover') : t(`common.${key}`),
    }))
    return items
  }, [t, handleClick, application])

  const handleFeaturesConfig = () => {
    workflowRef.current?.handleFeaturesConfig?.()
  }

  return (
    <>
      <PageHeader
        avatarText={application?.name?.trim()[0]}
        avatarClassName={clsx({
          'rb:bg-[#155EEF]': application?.type === 'agent',
          'rb:bg-[#9C6FFF]!': application?.type === 'multi_agent',
          'rb:bg-[#171719]': application?.type.includes('workflow'),
        })}
        title={application?.name || ''}
        operation={source !== 'sharing' && <Dropdown
          menu={{ items: formatMenuItems, onClick: handleClick }}
          trigger={['click']}
          placement="bottomRight"
        >
          <div
            className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit_active.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
          ></div>
        </Dropdown>}
        centerContent={<Flex justify="center" className="rb:h-16!">
          <Tabs
            activeKey={activeTab}
            items={formatTabItems}
            onChange={handleChangeTab}
            className={styles.tabs}
          />
        </Flex>}
        extra={application?.type.includes('workflow') && source !== 'sharing' && activeTab === 'arrangement'
          ? <Flex align="center" justify="end" gap={10} className="rb:h-8">
            <CheckList workflowRef={workflowRef} appId={application?.id ?? ''} />
            {application?.type === 'workflow' &&
              <Popover content={t('application.features')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
                <div
                  className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/features.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                  onClick={handleFeaturesConfig}
                ></div>
              </Popover>
            }
            <Popover content={t('workflow.clear')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/clear.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={clear}
              ></div>
            </Popover>
            {application?.type === 'workflow' &&
              <Popover content={t('workflow.addVariable')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
                <div
                  className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/variable.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                  onClick={addVariable}
                ></div>
              </Popover>
            }
            <Popover content={t('workflow.systemVariable')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/system.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={() => systemVariableModalRef.current?.handleOpen()}
              ></div>
            </Popover>
            <Popover content={t('workflow.addEnvVariable')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/env.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={addEnvVariable}
              ></div>
            </Popover>
            <Popover content={t('workflow.run')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/run.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={run}
              ></div>
            </Popover>
            <Popover content={t('workflow.save')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/save.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={save}
              ></div>
            </Popover>
            <Popover content={t('common.return')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
              <div
                className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('@/assets/images/workflow/return.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                onClick={goToApplication}
              ></div>
            </Popover>
          </Flex>
          : <Flex justify="flex-end">
            <Flex align="center" gap={8} className="rb:leading-5 rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={goToApplication}>
              <div
                className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/logout_grey.svg')]"
              ></div>
              {t('common.return')}
            </Flex>
          </Flex>
        }
      >
      </PageHeader>
      <ApplicationModal
        ref={applicationModalRef}
        refresh={refresh}
      />
      <CopyModal ref={copyModalRef} data={application as Application} />
      <UploadModal
        ref={uploadModalRef}
        refresh={refresh}
        id={id as string}
      />
      <SystemVariableModal
        ref={systemVariableModalRef}
        appType={application?.type}
      />
    </>
  );
};

export default ConfigHeader;