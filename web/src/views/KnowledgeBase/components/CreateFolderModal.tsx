import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';
import type { FolderFormData, KnowledgeBaseFormData, CreateFolderModalRef, CreateFolderModalRefProps } from '@/views/KnowledgeBase/types';
import RbModal from '@/components/RbModal'
import { createFolder, updateKnowledgeBase } from '@/api/knowledgeBase';
const CreateFolderModal = forwardRef<CreateFolderModalRef,CreateFolderModalRefProps>(({ 
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [folder, setFolder] = useState<FolderFormData>({} as FolderFormData);
  const [form] = Form.useForm<FolderFormData>();
  const [loading, setLoading] = useState(false)

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setFolder({} as FolderFormData);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (folder?: FolderFormData | null) => {
    debugger
    if (folder) {
      setFolder(folder);      
      // 设置表单值
      form.setFieldsValue({
        folder_name: folder.folder_name,
        parent_id: folder.parent_id ?? '',
        kb_id: folder.kb_id ?? '',
      });
    } else {
      // 新建时，重置表单并设置默认值
      form.resetFields();
      form.setFieldsValue({
        parent_id: '', 
        kb_id: ''
      });
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave =  () => {
    form
      .validateFields({ validateOnly: true })
      .then(async () => {
        setLoading(true)
        const formValues = form.getFieldsValue();
        const payload: FolderFormData = {
          ...formValues,
          parent_id: folder.parent_id ?? '',
          kb_id: folder.kb_id ?? '',
        }
        const updatePayload: KnowledgeBaseFormData = {
          id: folder.id ?? '',
          name: formValues.folder_name ?? '',
        }
        const data = await (folder.id ? updateKnowledgeBase(folder.id ?? '', updatePayload) : createFolder(payload)) as any;
        if(data) {
          if (refreshTable) {
            await refreshTable();
          }
          setLoading(false)
          handleClose()
        }else {
          setLoading(false)
        }    
      })
      .catch((err) => {
        console.log('err', err)
        setLoading(false)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  // 根据 type 获取标题
  const getTitle = () => {
    if (folder.id) {
      return t('common.edit') + ' ' + (folder.folder_name || '');
    }
    return t('knowledgeBase.createA') + ' ' + t('knowledgeBase.folder');
  }
  return (
    <RbModal
      title={getTitle()}
      open={visible}
      onCancel={handleClose}
      okText={folder.id ? t('common.save') : t('common.create')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        layout="vertical"
      >
        {/* <div className="rb:text-[14px] rb:font-medium rb:text-[#5B6167] rb:mb-[16px]">{t('model.basicParameters')}</div> */}
        <Form.Item
          name="folder_name"
          label={t('knowledgeBase.name')}
        >
          <Input placeholder={t('knowledgeBase.name')} />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default CreateFolderModal;