import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Switch, App, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ShareModalRef, SpaceItem} from '@/views/KnowledgeBase/types';
import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1'
import RbModal from '@/components/RbModal'
import kbIcon from '@/assets/images/knowledgeBase/knowledge-management.png';
import { updateKnowledgeBase, getWorkspaceAuthorizationList } from '@/api/knowledgeBase';
import { NoData } from './noData';
import type { ShareSpaceModalRef } from '@/views/KnowledgeBase/types';
import { formatDateTime } from '@/utils/format';
import ShareSpaceModal from './ShareSpaceModal'

const ShareModal = forwardRef<ShareModalRef>((_props, ref) => {
  const { t } = useTranslation();
  const shareSpaceModalRef = useRef<ShareSpaceModalRef>(null);
  const { message: messageApi } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)

  const [kbId, setKbId] = useState<string>('');
  const [knowledgeBase, setKnowledgeBase] = useState<V1KnowledgeListItem | null>(null);
  const [spaceList, setSpaceList] = useState<SpaceItem[]>([]);
 
  // Close modal and reset state
  const handleClose = () => {
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (kb_id?: string,knowledgeBase?: V1KnowledgeListItem | null) => {
    setKbId(kb_id ?? '');
    setKnowledgeBase(knowledgeBase ?? null);
    setVisible(true);
    getShareSpaceList(kb_id || '')
  };
  const getShareSpaceList = async(id: string) => {
      const response = await getWorkspaceAuthorizationList(id)
      setSpaceList(response?.items as any[]);
  }

  const handleShare = async() => {
    if (!knowledgeBase) return
    setLoading(true);
    try {
      const workspaceIds = spaceList
        .map(item => item.target_kb?.workspace_id)
        .filter(Boolean)
        .join(',');
      
      console.log('Workspace IDs:', workspaceIds);
      shareSpaceModalRef?.current?.handleOpen(kbId, null, workspaceIds);
      
      // Close modal after sharing
      handleClose();
    } finally {
      setLoading(false);
    }
  }
  const handleChange = (checked: boolean, item: any) => {
    // Toggle shared knowledge base status
    console.log('Switch changed:', checked, item);
    updateKnowledgeBase(item.target_kb?.id, {
      status: checked ? 1 : 2
    }).then(() => {
      if(checked){
        messageApi.success(t('knowledgeBase.shareSuccess'));
      }else{
        messageApi.success(t('knowledgeBase.stopShareSuccess'));
      }
      
      getShareSpaceList(kbId);
    }).catch(() => {
      messageApi.error(t('knowledgeBase.shareFailed'));
    })
  }

  // Expose methods to parent component
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
    handleShare
  }));

  return (
    <>
    <RbModal
      title={t('knowledgeBase.shareSpace')}
      open={visible}
      onCancel={handleClose}
      okText={t('knowledgeBase.share')}
      onOk={handleShare}
      confirmLoading={loading}
    >
        <Flex vertical className='rb:text-left'>
            <h4 className='rb:text-sm rb:font-medium rb:text-gray-800'>{t('knowledgeBase.shareSpaceTitle')}</h4>
            <span className='rb:text-xs rb:text-gray-500'>{t('knowledgeBase.shareSpaceNote')}</span>
            <Flex vertical gap={16} className='rb:text-left rb:mt-4!'>
              {spaceList.length === 0 && (
                <NoData />
              )}
              {spaceList.map((item,index) => (
                <Flex
                  key={index}
                  align="center"
                  justify="space-between"
                  gap={8}
                  className={`rb:border-gray-200 rb:rounded-lg rb:p-4! rb:border`}
                >
                  <Flex align="center" gap={8}>
                      <img src={item?.icon || kbIcon} className='rb:size-5' />
                      <Flex vertical gap={4} className='rb:text-left'>
                          <span className='rb:text-base rb:font-medium rb:text-gray-800'>{item.target_workspace?.name}</span>
                          <span className='rb:text-xs rb:text-gray-500'>{t('knowledgeBase.authorizedPerson')}:{item.shared_user?.username} {formatDateTime((item.target_workspace?.created_at || 0))}</span>
                      </Flex>
                  </Flex>
                  <div>
                    <Switch checkedChildren={t('common.enable')} unCheckedChildren={t('common.disable')} defaultChecked={item.target_kb?.status === 1} onChange={(checked) => handleChange(checked, item)} />
                  </div>
                </Flex>
              ))}
            </Flex>
        </Flex>
    </RbModal>
    <ShareSpaceModal 
      ref={shareSpaceModalRef} 
    />
    </>
  );
});

export default ShareModal;