import { forwardRef, useImperativeHandle, useState } from 'react';
import { Cascader, Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import type { FolderFormData, KnowledgeBaseFormData, CreateFolderModalRef, CreateFolderModalRefProps } from '@/views/KnowledgeBase/types';
import RbModal from '@/components/RbModal'
import { createFolder, getFolderList, updateKnowledgeBase } from '@/api/knowledgeBase';

interface DirectoryOption {
  value: string;
  label: string;
  isLeaf?: boolean;
  children?: DirectoryOption[];
}

const CreateFolderModal = forwardRef<CreateFolderModalRef,CreateFolderModalRefProps>(({ 
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [folder, setFolder] = useState<FolderFormData>({} as FolderFormData);
  const [form] = Form.useForm<FolderFormData>();
  const [loading, setLoading] = useState(false)
  const [directoryOpen, setDirectoryOpen] = useState(false);
  const [directoryOptions, setDirectoryOptions] = useState<DirectoryOption[]>([]);
  const [directoryValue, setDirectoryValue] = useState<string[]>([]);
  const [parentName, setParentName] = useState('');
  const [parentPath, setParentPath] = useState<string[]>([]);
  const loadDirectories = async (selectedOptions: DirectoryOption[]) => {
    const target = selectedOptions[selectedOptions.length - 1];
    if (!target || target.children) return;
    try {
      const response = await getFolderList({ kb_id: folder.kb_id, parent_id: target.value } as FolderFormData);
      const items = Array.isArray(response) ? response : response?.items ?? response?.list ?? response?.data?.items ?? [];
      target.children = items.filter((item: any) => {
        const ext = typeof item.file_ext === 'string' ? item.file_ext.trim().replace(/^\./, '').toLowerCase() : '';
        if (ext) return ext === 'folder';
        const type = typeof item.type === 'string' ? item.type.toLowerCase() : '';
        if (type === 'folder' || type === 'directory') return true;
        return item.is_directory ?? item.is_dir ?? Boolean(item.folder_name || item.children);
      }).map((item: any) => ({
        value: String(item.id ?? item.file_id ?? item.key ?? item.folder_id),
        label: item.folder_name ?? item.file_name ?? item.name ?? item.title ?? '未命名节点',
        isLeaf: false,
      }));
      target.isLeaf = target.children?.length === 0;
      setDirectoryOptions((options) => [...options]);
    } catch (error) {
      console.error('Failed to load folder options:', error);
    }
  };

  // Close modal and reset state
  const handleClose = () => {
    setFolder({} as FolderFormData);
    setDirectoryOpen(false);
    setParentName('');
    setParentPath([]);
    form.resetFields();
    setLoading(false)
    setVisible(false);
  };

  const handleOpen = (folder?: FolderFormData | null, _type?: string, initialParentPath?: string[]) => {
    form.resetFields();
    setDirectoryOpen(false);
    setParentName(folder?.parent_name || (folder?.parent_id === folder?.kb_id
      ? t('knowledgeBase.allFiles')
      : folder?.parent_id || ''));
    setParentPath(initialParentPath ?? (folder?.parent_name ? folder.parent_name.split(' / ') : []));
    setDirectoryOptions(folder?.kb_id ? [{
      value: folder.kb_id,
      label: t('knowledgeBase.allFiles'),
      isLeaf: false,
    }] : []);
    setDirectoryValue(folder?.parent_id || folder?.kb_id ? [folder.parent_id || folder.kb_id || ''] : []);
    setFolder(folder || {} as FolderFormData);
    if (folder) {
      setFolder(folder);      
      // Set form values
      form.setFieldsValue({
        folder_name: folder.folder_name,
        parent_id: folder.parent_id || folder.kb_id || '',
        kb_id: folder.kb_id ?? '',
      });
    } else {
      // Reset form and set default values for new folder
      form.setFieldsValue({
        parent_id: undefined, 
        kb_id: undefined
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
          parent_id: formValues.parent_id || folder.parent_id || folder.kb_id || '',
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
        {!folder.id && folder.kb_id && (
          <Form.Item label={t('knowledgeBase.parentDirectory')}>
            <Form.Item name="parent_id" hidden>
              <Input />
            </Form.Item>
            <Cascader<DirectoryOption>
              className="rb:w-full"
              open={directoryOpen}
              onOpenChange={setDirectoryOpen}
              options={directoryOptions}
              value={directoryValue}
              changeOnSelect
              allowClear={false}
              loadData={loadDirectories}
              displayRender={() => parentPath.join(' / ') || parentName}
              onChange={(values, options) => {
                setDirectoryValue(values as string[]);
                setParentPath(options.map((option) => option.label));
                form.setFieldValue('parent_id', values[values.length - 1]);
              }}
            />
          </Form.Item>
        )}
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