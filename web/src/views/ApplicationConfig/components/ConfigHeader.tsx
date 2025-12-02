import { type FC, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Layout, Tabs, Dropdown } from 'antd';
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
import type { CopyModalRef } from '../types'
import { deleteApplication } from '@/api/application'
import CopyModal from './CopyModal'

const { Header } = Layout;

const tabKeys = ['arrangement', 'api', 'release']
const menuIcons: Record<string, string> = {
  edit: editIcon,
  copy: copyIcon,
  export: exportIcon,
  delete: deleteIcon
}
interface ConfigHeaderProps {
  application?: Application;
  activeTab: string;
  handleChangeTab: (key: string) => void;
  refresh: () => void;
}
const ConfigHeader: FC<ConfigHeaderProps> = ({ application, activeTab, handleChangeTab, refresh }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams();
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const copyModalRef = useRef<CopyModalRef>(null);

  const formatTabItems = () => {
    return tabKeys.map(key => ({
      key,
      label: t(`application.${key}`),
    }))
  }
  const formatMenuItems = () => {
    const items =  ['edit', 'copy', 'delete'].map(key => ({
      key,
      icon: <img src={menuIcons[key]} className="rb:w-[16px] rb:h-[16px] rb:mr-[8px]" />,
      label: t(`common.${key}`),
    }))
    return {
      items,
      onClick: handleClick
    }
  }
  const handleClick: MenuProps['onClick'] = ({ key }) => {
    console.log('key', key)
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
  const goToApplication = () => {
    navigate('/application', { replace: true })
  }

  return (
    <>
      <Header className="rb:w-full rb:h-[64px] rb:grid rb:grid-cols-3 rb:p-[16px_16px_16px_24px]! rb:border-b rb:border-[#EAECEE] rb:leading-[32px]">
        <div className="rb:h-[32px] rb:flex rb:items-center rb:font-medium">
          <div className="rb:w-[32px] rb:h-[32px] rb:rounded-[8px] rb:mr-[13px] rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[24px] rb:text-[#ffffff]">
            {application?.name[0]}
          </div>
          
          <div className="rb:max-w-[100%-80px] rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{application?.name}</div>
          <Dropdown 
            menu={formatMenuItems()} 
            trigger={['click']}
            placement="bottomRight"
          >
            <div 
              className="rb:w-[20px] rb:h-[20px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
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
        <div className="rb:h-[32px] rb:flex rb:items-center rb:justify-end rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={goToApplication}>
          <img src={logoutIcon} className="rb:mr-[8px]" />
          {t('application.returnToApplicationList')}
        </div>
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