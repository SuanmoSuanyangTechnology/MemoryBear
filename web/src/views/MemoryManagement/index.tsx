import React, { useState, useEffect, useRef } from 'react';
import { List, Button, Space, App, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import MemoryForm from './components/MemoryForm';
import type { Memory, MemoryFormRef } from '@/views/MemoryManagement/types'
import RbCard from '@/components/RbCard/Card'
// import StatusTag from '@/components/StatusTag'
import { getMemoryConfigList, deleteMemoryConfig } from '@/api/memory'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import { formatDateTime } from '@/utils/format';
import clsx from 'clsx'

const MemoryManagement: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [data, setData] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);

  const memoryFormRef = useRef<MemoryFormRef>(null);

  useEffect(() => {
    loadMoreData()
  }, []);
  
  const loadMoreData = () => {
    setLoading(true);
    getMemoryConfigList()
      .then((res) => {
        const response = res as Memory[];
        const results = Array.isArray(response) ? response : [];
        setData(results);
      })
      .catch(() => {
        console.error('Failed to load data');
      })
      .finally(() => {
        setLoading(false);
      });
  };

  // 打开新增标签弹窗
  const handleEdit = (config?: Memory) => {
    memoryFormRef.current?.handleOpen(config);
  }
  const handleDelete = (item: Memory) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.config_name }),
      okText: t('common.delete'),
      okType: 'danger',
      onOk: () => {
        deleteMemoryConfig(item.config_id)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            loadMoreData();
          })
      }
    })
  };

  const handleClick = (id: number, type: string) => {
    switch (type) {
      case 'memoryExtractionEngine':
        navigate(`/memory-extraction-engine/${id}`)
        break
      case 'forgottenEngine':
        navigate(`/forgetting-engine/${id}`)
        break
      case 'emotionEngine':
        navigate(`/emotion-engine/${id}`)
        break;
      case 'reflectionEngine':
        navigate(`/reflection-engine/${id}`)
        break;
    }
  }

  return (
    <>
      <div className="rb:text-right rb:mb-4">
        <Button type="primary" onClick={() => handleEdit()}>
          {t('memory.createConfiguration')}
        </Button>
      </div>
      
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <List
          grid={{ gutter: 16, column: 2 }}
          loading={loading}
          dataSource={data}
          renderItem={(item) => (
            <List.Item key={item.config_id}>
              <RbCard 
                title={item.config_name}
              >
                <Tooltip title={item.config_desc}>
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:-mt-1 rb:wrap-break-word rb:line-clamp-1">{item.config_desc}</div>
                </Tooltip>

                <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-3">
                  {['memoryExtractionEngine', 'forgottenEngine', 'emotionEngine', 'reflectionEngine'].map((key) => (
                    <div key={key} className="rb:group rb:cursor-pointer rb:bg-[#F0F3F8] rb:h-10 rb:rounded-md rb:flex rb:items-center rb:justify-between rb:p-[0_8px_0_12px] rb:mt-3 rb:text-[#5B6167] rb:font-medium"
                      onClick={() => handleClick(item.config_id, key)}
                    >
                      {t(`memory.${key}`)}
                      <span className='rb:flex rb:items-center rb:justify-end'>
                        {/* <StatusTag status={item[key] === 'active' ? 'success' : 'error'} text={item[key] === 'active' ? t('memory.active') : t('memory.inactive')} /> */}
                        <div 
                          className="rb:w-4 rb:h-4 rb:-ml-0.75 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/memory/arrow_right.svg')] rb:group-hover:bg-[url('@/assets/images/memory/arrow_right_hover.svg')]" 
                        ></div>
                      </span>
                    </div>
                  ))}
                </div>
                <div className={clsx("rb:mt-4 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167] rb:flex rb:items-center", {
                  'rb:justify-between': item.updated_at,
                  'rb:justify-end': !item.updated_at
                })}>
                  {formatDateTime(item.updated_at, 'YYYY-MM-DD HH:mm:ss')}
                  <Space size={16}>
                    <div 
                      className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
                      onClick={() => handleEdit(item)}
                    ></div>
                    <div 
                      className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]" 
                      onClick={() => handleDelete(item)}
                    ></div>
                  </Space>
                </div>
              </RbCard>
            </List.Item>
          )}
        />
      </BodyWrapper>

      <MemoryForm
        ref={memoryFormRef}
        refresh={loadMoreData}
      />
    </>
  );
};

export default MemoryManagement;