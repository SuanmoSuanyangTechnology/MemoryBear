/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-10 18:52:55
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-03 18:44:58
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import { message } from 'antd';
import type { ShareModalRef, ShareModalRefProps, KnowledgeBase} from '../types';
import RbModal from '@/components/RbModal'
// import betchControlIcon from '@/assets/images/knowledgeBase/betch-control.png';
import kbIcon from '@/assets/images/knowledgeBase/knowledge-management.png';
// import robotIcon from '@/assets/images/knowledgeBase/robot.png';
import { getSpaceList, shareKnowledgeBase } from '../service';
import { NoData } from './noData';
import type { SpaceItem } from '../types';
import { formatDateTime } from '@/utils/format';
const ShareModal = forwardRef<ShareModalRef,ShareModalRefProps>(({ handleShare: onShare }, ref) => {
  const { t } = useTranslation();
  const [messageApi, contextHolder] = message.useMessage();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [curIndex, setCurIndex] = useState(-1);
  const [kbId, setKbId] = useState<string>('');
  const [spaceIds, setSpaceIds] = useState<string>('');
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [spaceList, setSpaceList] = useState<SpaceItem[]>([]);
 
  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setCurIndex(-1);
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (kb_id?: string,knowledgeBase?: KnowledgeBase | null, spaceIds?:string) => {
    setKbId(kb_id ?? '');
    setSpaceIds(spaceIds ?? '')
    setKnowledgeBase(knowledgeBase ?? null);
    setVisible(true);
    getSpaceListFn(spaceIds ?? '')
  };
  const getSpaceListFn = async (ids:string) => {
    const response = await getSpaceList();
    const filteredItems = response.items.filter(item => !ids.includes(item.id));
    setSpaceList(filteredItems as SpaceItem[]);
  }
  const handleShare = async() => {

    // 获取所有 checked 为 true 的数据
    const checkedItems = spaceList.filter(item => item.is_active);
    debugger
    // 获取当前选中的项（curIndex 对应的数据）
    const selectedItem = curIndex !== -1 ? spaceList[curIndex] : null;
    if(!selectedItem){
      messageApi.error(t('knowledgeBase.selectSpace'));
      return;
    }
    const payload = {
      source_kb_id: kbId ?? '',
      target_workspace_id: selectedItem?.id ?? '',
    }
    const respose = await shareKnowledgeBase(payload)
    if(respose){
      messageApi.success(t('knowledgeBase.shareSuccess'));
    }else{
      messageApi.error(t('knowledgeBase.shareFailed'));
    }
    // 调用父组件传递的回调函数，传递选中的数据
    onShare?.({
      checkedItems,
      selectedItem
    });
    
    // 分享后关闭弹窗
    handleClose();
  }
  const handleClick = (index: number, checked: boolean) => {
    if (!checked) return;
    setCurIndex(index);
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
                      className={`rb:flex rb:items-center rb:justify-between ${curIndex === index ? 'rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF]' : 'rb:border-gray-200'} ${item.is_active ? 'rb:cursor-pointer rb:hover:bg-[rgba(21,94,239,0.06)] rb:hover:border-[#155EEF]' : 'rb:cursor-not-allowed rb:bg-[#F9F9F9]'} rb:gap-2 rb:rounded-lg rb:p-4 rb:border`}
                      onClick={item.is_active ? () => handleClick(index, item.is_active) : undefined}
                  >
                    <div className='rb:flex rb:items-center rb:gap-2'>
                        <img src={item.icon || kbIcon} className='rb:size-[20px]' />
                        <div className='rb:flex rb:flex-col rb:text-left rb:gap-1'>
                            <span className='rb:text-base rb:font-medium rb:text-gray-800'>{item.name}</span>
                        </div>
                    </div>
                  </div>
              ))}
            </div>
        </div>
    </RbModal>
    </>
  );
});

export default ShareModal;