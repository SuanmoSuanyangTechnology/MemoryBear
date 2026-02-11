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

  // Close modal and reset state
  const handleClose = () => {
    setFolder({} as FolderFormData);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (folder?: FolderFormData | null) => {
    if (folder) {
      setFolder(folder);      
      // Set form values
      form.setFieldsValue({
        folder_name: folder.folder_name,
        parent_id: folder.parent_id ?? '',
        kb_id: folder.kb_id ?? '',
      });
    } else {
      // Reset form and set default values for new folder
      form.resetFields();
      form.setFieldsValue({
        parent_id: '', 
        kb_id: ''
      });
    }
    setVisible(true);
  };
  // Save form data and submit
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

  // Expose methods to parent component
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  // Get modal title based on folder state
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