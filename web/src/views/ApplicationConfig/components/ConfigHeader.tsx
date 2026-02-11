/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:27:52 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:27:52 
 */
import { type FC, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Layout, Tabs, Dropdown, Button, Flex } from 'antd';
import type { MenuProps } from 'antd';
import { useTranslation } from 'react-i18next';

import styles from '../index.module.css'
import logoutIcon from '@/assets/images/logout.svg'
import editIcon from '@/assets/images/edit_hover.svg'
import copyIcon from '@/assets/images/copy_hover.svg'
import exportIcon from '@/assets/images/export_hover.svg'
import deleteIcon from '@/assets/images/delete_hover.svg'
import type { Application, ApplicationModalRef } from '@/views/ApplicationManagement/types';
import ApplicationModal from '@/views/ApplicationManagement/components/ApplicationModal'
import type { CopyModalRef, AgentRef, ClusterRef, WorkflowRef } from '../types'
import { deleteApplication } from '@/api/application'
import CopyModal from './CopyModal'

const { Header } = Layout;

/**
 * Tab keys for application configuration
 */
const tabKeys = ['arrangement', 'api', 'release', 'statistics']

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
}

/**
 * Configuration header component
 * Displays application name, tabs, and action buttons
 */
const ConfigHeader: FC<ConfigHeaderProps> = ({ 
  application, activeTab, handleChangeTab, refresh,
  workflowRef,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const copyModalRef = useRef<CopyModalRef>(null);

  /**
   * Format tab items for display
   */
  const formatTabItems = () => {
    return tabKeys.map(key => ({
      key,
      label: t(`application.${key}`),
    }))
  }
  /**
   * Format dropdown menu items
   */
  const formatMenuItems = () => {
    const items =  ['edit', 'copy', 'export', 'delete'].map(key => ({
      key,
      icon: <img src={menuIcons[key]} className="rb:w-4 rb:h-4 rb:mr-2" />,
      label: t(`common.${key}`),
    }))
    return {
      items,
      onClick: handleClick
    }
  }
  /**
   * Handle menu item click
   */
  const handleClick: MenuProps['onClick'] = ({ key }) => {
    switch (key) {
      case 'edit':
        applicationModalRef.current?.handleOpen(application as Application)
        break;
      case 'copy':
        copyModalRef.current?.handleOpen()
        break;
      case 'export':
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
  return (
    <>
      <Header className="rb:w-full rb:h-16 rb:grid rb:grid-cols-3 rb:p-[16px_16px_16px_24px]! rb:border-b rb:border-[#EAECEE] rb:leading-8">
        <div className="rb:h-8 rb:flex rb:items-center rb:font-medium">
          <div className="rb:w-8 rb:h-8 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[24px] rb:text-[#ffffff]">
            {application?.name[0]}
          </div>
          
          <div className="rb:max-w-[100%-80px] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{application?.name}</div>
          <Dropdown 
            menu={formatMenuItems()} 
            trigger={['click']}
            placement="bottomRight"
          >
            <div 
              className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
            ></div>
          </Dropdown>
        </div>
        
        <div className="rb:flex rb:justify-center">
          <Tabs 
            activeKey={activeTab} 
            items={formatTabItems()} 
            onChange={handleChangeTab} 
            className={styles.tabs}
          />
        </div>
        {application?.type === 'workflow'
        ? <div className="rb:h-8 rb:flex rb:items-center rb:justify-end rb:gap-2.5">
            <Button onClick={clear}>{t('workflow.clear')}</Button>
            <Button onClick={addvariable}>{t('workflow.addvariable')}</Button>
            <Button onClick={run}>{t('workflow.run')}</Button>
            <Button type="primary" onClick={save}>{t('workflow.save')}</Button>
            {/* <Button type="primary">{t('workflow.export')}</Button> */}
            <img src={logoutIcon} className="rb:w-4 rb:h-4 rb:cursor-pointer" onClick={goToApplication} />
          </div>
          : <Flex justify="flex-end">
            <div className="rb:h-8 rb:flex rb:items-center rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={goToApplication}>
              <img src={logoutIcon} className="rb:mr-2 rb:w-4 rb:h-4" />
              {t('application.returnToApplicationList')}
            </div>
          </Flex>
        }
      </Header>
      <ApplicationModal
        ref={applicationModalRef}
        refresh={refresh}
      />
      <CopyModal ref={copyModalRef} data={application as Application} />
    </>
  );
};

export default ConfigHeader;