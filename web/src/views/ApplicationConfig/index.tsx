import React, { useEffect, useState, useRef } from 'react';
import { useParams } from 'react-router-dom';
import ConfigHeader from './components/ConfigHeader'
import type { AgentRef } from './types'
import type { Application } from '@/views/ApplicationManagement/types'
import Agent from './Agent'
import Api from './Api'
import ReleasePage from './ReleasePage'
import Cluster from './Cluster'
import { getApplication } from '@/api/application'
import { randomString } from '@/utils/common'

const apiKeyList = [`app-${randomString(24, false)}`]
const ApplicationConfig: React.FC = () => {
  const { id } = useParams();
  const agentRef = useRef<AgentRef>(null)
  const [application, setApplication] = useState<Application | null>(null);
  const [activeTab, setActiveTab] = useState('arrangement');

  const handleChangeTab = async (key: string) => {
    if (activeTab === 'arrangement' && application?.type === 'agent' && agentRef.current) {
      agentRef.current.handleSave(false)
        .then(() => {
            setActiveTab(key)
        })
    } else {
      setActiveTab(key)
    }
  }

  useEffect(() => {
    getApplicationInfo()
  }, [id])

  const getApplicationInfo = () => {
    if (!id) {
      return
    }
    getApplication(id as string).then(res => {
      const response = res as Application
      setApplication(response)
    })
  }

  return (
    <>
      <ConfigHeader 
        activeTab={activeTab}
        handleChangeTab={handleChangeTab}
        application={application as Application}
        refresh={getApplicationInfo}
      />
      {activeTab === 'arrangement' && application?.type === 'agent' && <Agent ref={agentRef} />}
      {activeTab === 'arrangement' && application?.type === 'multi_agent' && <Cluster application={application as Application} />}
      {activeTab === 'api' && <Api apiKeyList={apiKeyList} />}
      {activeTab === 'release' && <ReleasePage data={application as Application} refresh={getApplicationInfo} />}
    </>
  );
};

export default ApplicationConfig;
