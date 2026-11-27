/**
 * v1 create folder modal
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

import { createKnowledgeBaseV1, patchKnowledgeBaseV1 } from '@/api/knowledgeBaseV1';
import type { V1FolderTreeNode } from '@/views/KnowledgeBase/typesV1';
import RbModal from '@/components/RbModal';

export interface CreateFolderV1ModalRef {
  handleOpen: (parentId?: string) => void;
  handleEdit: (folder: V1FolderTreeNode) => void;
}

interface CreateFolderV1ModalProps {
  refresh?: () => void;
}

interface FolderFormValues {
  name: string;
  description?: string;
}

const CreateFolderV1Modal = forwardRef<CreateFolderV1ModalRef, CreateFolderV1ModalProps>(
  ({ refresh }, ref) => {
    const { t } = useTranslation();
    const [form] = Form.useForm<FolderFormValues>();
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [parentId, setParentId] = useState<string | undefined>();
    const [editingId, setEditingId] = useState<string | undefined>();

    useImperativeHandle(ref, () => ({
      handleOpen: (id?: string) => {
        setEditingId(undefined);
        setParentId(id);
        form.resetFields();
        setOpen(true);
      },
      handleEdit: (folder: V1FolderTreeNode) => {
        setEditingId(folder.id);
        setParentId(undefined);
        form.setFieldsValue({ name: folder.name });
        setOpen(true);
      },
    }));

    const handleOk = () => {
      form.validateFields().then((values) => {
        setLoading(true);
        const request = editingId
          ? patchKnowledgeBaseV1(editingId, { name: values.name })
          : createKnowledgeBaseV1({
            name: values.name,
            type: 'Folder',
            parent_id: parentId ?? null,
            description: values.description ?? null,
          });
        request
          .then(() => {
            refresh?.();
            setOpen(false);
          })
          .finally(() => setLoading(false));
      });
    };

    return (
      <RbModal
        open={open}
        title={editingId ? t('knowledgeBase.editDirectory') : t('knowledgeBase.createFolder')}
        onCancel={() => setOpen(false)}
        onOk={handleOk}
        confirmLoading={loading}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t('knowledgeBase.name')}
            rules={[{ required: true, message: t('knowledgeBase.nameRequired') }]}
          >
            <Input maxLength={50} placeholder={t('knowledgeBase.name')} />
          </Form.Item>
          {!editingId && (
            <Form.Item name="description" label={t('knowledgeBase.description')}>
              <Input.TextArea maxLength={500} rows={3} placeholder={t('knowledgeBase.description')} />
            </Form.Item>
          )}
        </Form>
      </RbModal>
    );
  },
);

export default CreateFolderV1Modal;
