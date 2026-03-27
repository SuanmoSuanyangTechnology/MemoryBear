/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:52 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 17:33:44
 */
import { type FC, useRef, useMemo, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Tabs, Dropdown, Button, Flex } from 'antd';
import type { MenuProps } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import styles from '../index.module.css'
import editIcon from '@/assets/images/edit_hover.svg'
import copyIcon from '@/assets/images/copy_hover.svg'
import exportIcon from '@/assets/images/export_hover.svg'
import deleteIcon from '@/assets/images/delete_hover.svg'
import type { Application, ApplicationModalRef } from '@/views/ApplicationManagement/types';
import ApplicationModal from '@/views/ApplicationManagement/components/ApplicationModal'
import type { CopyModalRef, AgentRef, ClusterRef, WorkflowRef, FeaturesConfigForm } from '../types'
import { deleteApplication, appExport } from '@/api/application'
import CopyModal from './CopyModal'
import PageHeader from '@/components/Layout/PageHeader'
import FeaturesConfig from './FeaturesConfig'

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
  edit: editIcon,
  copy: copyIcon,
  export: exportIcon,
  delete: deleteIcon
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
  /** Callback to update features in parent */
  onFeaturesChange?: (value: FeaturesConfigForm) => void;
}

/**
 * Configuration header component
 * Displays application name, tabs, and action buttons
 */
const ConfigHeader: FC<ConfigHeaderProps> = ({ 
  application, activeTab, handleChangeTab, refresh,
  workflowRef,
  appRef,
  features,
  onFeaturesChange,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id, source } = useParams();
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const copyModalRef = useRef<CopyModalRef>(null);

  /**
   * Format tab items for display
   */
  const formatTabItems = useMemo(() => {
    return (source === 'sharing' ? sharingTabKeys : tabKeys).map(key => ({
      key,
      label: t(`application.${key}`),
    }))
  }, [source, sharingTabKeys, tabKeys])
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
  const addvariable = () => {
    workflowRef?.current?.addVariable()
  }
  /**
   * Format dropdown menu items
   */
  const formatMenuItems = useMemo(() => {
    const items = (application?.type !== 'multi_agent' ? ['edit', 'copy', 'export', 'delete'] : ['edit', 'copy', 'delete']).map(key => ({
      key,
      icon: <img src={menuIcons[key]} className="rb:w-4 rb:h-4 rb:mr-2" />,
      label: t(`common.${key}`),
    }))
    return items
  }, [t, handleClick, application])

  const handleSaveFeaturesConfig = useCallback((value: FeaturesConfigForm) => {
    appRef?.current?.handleSaveFeaturesConfig?.(value)
    onFeaturesChange?.(value)
  }, [appRef, onFeaturesChange])
  
  return (
    <>
      <PageHeader
        avatarText={application?.name?.trim()[0]}
        avatarClassName={clsx({
          'rb:bg-[#155EEF]': application?.type === 'agent',
          'rb:bg-[#9C6FFF]!': application?.type === 'multi_agent',
          'rb:bg-[#171719]': application?.type === 'workflow',
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
        extra={application?.type === 'workflow' && source !== 'sharing' && activeTab === 'arrangement'
          ? <Flex align="center" justify="end" gap={10} className="rb:h-8">
            <FeaturesConfig
              source={application?.type}
              value={features as FeaturesConfigForm}
              refresh={handleSaveFeaturesConfig}
              chatVariables={(workflowRef.current?.chatVariables || []).map(v => ({ ...v, display_name: v.name }))}
            />
            <Button onClick={clear}>{t('workflow.clear')}</Button>
            <Button onClick={addvariable}>{t('workflow.addvariable')}</Button>
            <Button onClick={run}>{t('workflow.run')}</Button>
            <Button type="primary" onClick={save}>{t('workflow.save')}</Button>
            <div
              className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/logout.svg')]"
              onClick={goToApplication}
            ></div>
          </Flex>
          : <Flex justify="flex-end">
            <Flex align="center" className="rb:leading-5 rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={goToApplication}>
              <div
                className="rb:mr-2 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/logout.svg')]"
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
    </>
  );
};

export default ConfigHeader;