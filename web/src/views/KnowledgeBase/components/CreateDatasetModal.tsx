/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2025-11-10 18:52:55
 * @LastEditors: yujiangping
 * @LastEditTime: 2025-12-29 16:09:13
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import type { RadioChangeEvent } from 'antd';
import { Flex, Radio } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { CreateDatasetModalRef, CreateDatasetModalRefProps} from '@/views/KnowledgeBase/types';
import RbModal from '@/components/RbModal'
const style: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 16,
};
const radioWrapperBaseStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  columnGap: 14, // 点与文字更宽的间距
  width: '100%',
  border: '1px solid #E5E5E5',
  borderRadius: 8,
  padding: 16,
};
const getActiveRadioStyle = (active: boolean): React.CSSProperties => ({
  ...radioWrapperBaseStyle,
  border: active ? '1px solid #1677ff' : radioWrapperBaseStyle.border,
});
const CreateDatasetModal = forwardRef<CreateDatasetModalRef,CreateDatasetModalRefProps>((_props, ref) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // const { knowledgeBaseId } = useParams<{ knowledgeBaseId: string }>();
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string | undefined>(undefined);
  const [parentId, setParentId] = useState<string | undefined>(undefined);
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [value, setValue] = useState(0);
  // const { handleCreateDataset: onCreate } = props || {};
  const items = [
    {
      title: t('knowledgeBase.localFile'),
      description: t('knowledgeBase.uploadFileTypes'),
    },
    // 暂时隐藏
    // {
    //   title: t('knowledgeBase.webLink'),
    //   description: t('knowledgeBase.readStaticWebPage')
    // },
    {
      title: t('knowledgeBase.customText'),
      description: t('knowledgeBase.manuallyInputText')
    },
  ]
  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (kb_id?: string,parent_id?: string) => {
    setKnowledgeBaseId(kb_id);
    setParentId(parent_id);
    setVisible(true);
  };

  const handleCreateDataset = () => {
    // // 获取所有 checked 为 true 的数据
    // const checkedItems = testData.filter(item => item.checked);
    // // 获取当前选中的项（curIndex 对应的数据）
    // const selectedItem = curIndex !== 9999 ? testData[curIndex] : null;
    
    // // 调用父组件传递的回调函数，传递选中的数据
    // onShare?.({
    //   checkedItems,
    //   selectedItem
    // })；
    // const selected = items[value];
    // onCreate?.({
    //   value,
    //   title: selected.title,
    //   description: selected.description,
    // });
    // 跳转到创建数据集页面并携带来源参数
    const source = value === 0 ? 'local' : value === 1 ? 'link' : 'text';
    if (knowledgeBaseId) {
      navigate(`/knowledge-base/${knowledgeBaseId}/create-dataset`,{
        state: {
          source: source,
          knowledgeBaseId: knowledgeBaseId,
          parentId: parentId,
        }
      });
    }
    // 关闭弹窗
    handleClose();
  }
  const onChange = (e: RadioChangeEvent) => {
    setValue(e.target.value);
  };
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose,
    handleCreateDataset
  }));

  return (
    <RbModal
      title={t('knowledgeBase.createA') + ' ' + t('knowledgeBase.dataset')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.create')}
      onOk={handleCreateDataset}
      confirmLoading={loading}
    >
        <div className='rb:flex rb:flex-col rb:text-left'>
            <h4 className='rb:text-sm rb:font-medium rb:text-gray-800'>{t('knowledgeBase.selectSource')}</h4>
            <div className='rb:flex rb:flex-col rb:text-left rb:gap-4 rb:mt-4 '>
              <Radio.Group onChange={onChange} value={value} style={style}>
                <Radio value={0} style={getActiveRadioStyle(value === 0)} className='rb:w-full'>
                  <Flex gap="small" align='start' justify='start' vertical>
                    <span className='rb:text-base rb:font-medium rb:text-gray-800'>{items[0].title}</span>
                    <span className='rb:text-xs rb:text-gray-500'>{items[0].description}</span>
                  </Flex>
                </Radio>
                {/* <Radio value={1} style={getActiveRadioStyle(value === 1)} className='rb:w-full'>
                  <Flex gap="small" align='start' justify='start' vertical>
                    <span className='rb:text-base rb:font-medium rb:text-gray-800'>{items[1].title}</span>
                    <span className='rb:text-xs rb:text-gray-500'>{items[1].description}</span>
                  </Flex>
                </Radio> */}
                <Radio value={2} style={getActiveRadioStyle(value === 2)} className='rb:w-full'>
                  <Flex gap="small" align='start' justify='start' vertical>
                    <span className='rb:text-base rb:font-medium rb:text-gray-800'>{items[1].title}</span>
                    <span className='rb:text-xs rb:text-gray-500'>{items[1].description}</span>
                  </Flex>
                </Radio> 
              </Radio.Group>
            </div>
        </div>
    </RbModal>
  );
});

export default CreateDatasetModal;