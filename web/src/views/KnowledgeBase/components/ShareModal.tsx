/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-10 18:52:55
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-11-29 12:29:31
 */
import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import { message } from 'antd';
import type { ShareModalRef, ShareModalRefProps, KnowledgeBase} from '../types';
import RbModal from '@/components/RbModal'
// import betchControlIcon from '@/assets/images/knowledgeBase/betch-control.png';
import kbIcon from '@/assets/images/knowledgeBase/knowledge-management.png';
// import robotIcon from '@/assets/images/knowledgeBase/robot.png';
import { updateKnowledgeBase, getWorkspaceAuthorizationList } from '../service';
import { NoData } from './noData';
import type { ListQuery, ShareSpaceModalRef } from '../types';
import { formatDateTime } from '@/utils/format';
import ShareSpaceModal from './ShareSpaceModal'
const ShareModal = forwardRef<ShareModalRef,ShareModalRefProps>(({ handleShare: onShare }, ref) => {
  const { t } = useTranslation();
  const shareSpaceModalRef = useRef<ShareSpaceModalRef>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [curIndex, setCurIndex] = useState(9999);
  const [query, setQuery] = useState<ListQuery>({});

  const [kbId, setKbId] = useState<string>('');
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [spaceList, setSpaceList] = useState<SpaceItem[]>([]);
 
  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setCurIndex(9999);
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (kb_id?: string,knowledgeBase?: KnowledgeBase | null) => {
    setKbId(kb_id ?? '');
    setKnowledgeBase(knowledgeBase ?? null);
    setVisible(true);
    getShareSpaceList(kb_id || '')
    // getSpaceListFn()
  };
  const getShareSpaceList = async(id: string) => {
      try{
        const response = await getWorkspaceAuthorizationList(id)
        setSpaceList(response?.items as any[]);
      } catch (error) {
        messageApi.error(t('knowledgeBase.shareFailed'));
      }
      
  }

  const handleShare = async() => {
    const workspaceIds = spaceList
      .map(item => item.target_kb?.workspace_id)
      .filter(Boolean)
      .join(',');
    
    console.log('Workspace IDs:', workspaceIds);
    shareSpaceModalRef?.current?.handleOpen(kbId,knowledgeBase,workspaceIds);
    
    // 分享后关闭弹窗
    handleClose();
  }
  const handleChange = (checked: boolean, item: any) => {
    // 打开/关闭分享出去的数据库
    console.log('Switch changed:', checked, item);
    updateKnowledgeBase(item.target_kb?.id, {
      status: checked ? 1 : 2
    }).then(() => {
      messageApi.success(t('knowledgeBase.shareSuccess'));
      getShareSpaceList(kbId);
    }).catch(() => {
      messageApi.error(t('knowledgeBase.shareFailed'));
    })
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
    handleShare
  }));

  return (
    <>
    {contextHolder}
    <RbModal
      title={t('knowledgeBase.toWorkspace')}
      open={visible}
      onCancel={handleClose}
      okText={t('knowledgeBase.share')}
      onOk={handleShare}
      confirmLoading={loading}
    >
        <div className='rb:flex rb:flex-col rb:text-left'>
            <h4 className='rb:text-sm rb:font-medium rb:text-gray-800'>{t('knowledgeBase.shareTitle')}</h4>
            <span className='rb:text-xs rb:text-gray-500'>{t('knowledgeBase.shareNote')}</span>
            <div className='rb:flex rb:flex-col rb:text-left rb:gap-4 rb:mt-4 '>
              {spaceList.length === 0 && (
                <NoData />
              )}
              {spaceList.map((item,index) => (
                  <div key={index} 
                      className={`rb:flex rb:items-center rb:justify-between rb:border-gray-200 rb:gap-2 rb:rounded-lg rb:p-4 rb:border`}
                     
                  >
                    <div className='rb:flex rb:items-center rb:gap-2'>
                        <img src={item.icon || kbIcon} className='rb:size-[20px]' />
                        <div className='rb:flex rb:flex-col rb:text-left rb:gap-1'>
                            <span className='rb:text-base rb:font-medium rb:text-gray-800'>{item.target_workspace?.name}</span>
                            <span className='rb:text-xs rb:text-gray-500'>{t('knowledgeBase.authorizedPerson')}:{item.shared_user?.username} {formatDateTime((item.target_workspace?.created_at || 0))}</span>
                        </div>
                    </div>
                    <div>
                      <Switch checkedChildren={t('common.enable')} unCheckedChildren={t('common.disable')} defaultChecked={item.target_kb?.status === 1} onChange={(checked) => handleChange(checked, item)} />
                    </div>
                  </div>
              ))}
            </div>
        </div>
    </RbModal>
    <ShareSpaceModal 
      ref={shareSpaceModalRef} 
    />
    </>
  );
});

export default ShareModal;