import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, Select, Modal, App } from 'antd';
import type { SelectProps } from 'antd';
import { useTranslation } from 'react-i18next';
import copy from 'copy-to-clipboard'

import type { MemberModalData, Member, MemberModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { inviteMember, updateMember } from '@/api/member'

const FormItem = Form.Item;
const { Option } = Select;
type LabelRender = SelectProps['labelRender'];

interface MemberModalProps {
  refreshTable: () => void;
}

const MemberModal = forwardRef<MemberModalRef, MemberModalProps>(({
  refreshTable
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const initialForm = {
    // role: 'member',
  }
  const [visible, setVisible] = useState(false);
  const [editingUser, setEditingUser] = useState<Member | null>(null);
  const [form] = Form.useForm<MemberModalData>();
  const [loading, setLoading] = useState(false)
  const [modal, contextHolder] = Modal.useModal();

  const roleOptions = [
    'member',
    'manager'
  ]
  const values: MemberModalData = Form.useWatch([], form);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    setEditingUser(null);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = (member?: Member | null) => {
    if (member) {
      setEditingUser(member);
      // 设置表单值
      form.setFieldsValue({
        email: member.account,
        role: member.role
      });
    } else {
      form.resetFields();
    }
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        setLoading(true)
        const response = editingUser?.id 
          ? updateMember({
            role: values.role,
            id: editingUser?.id
          }) : inviteMember(values)
          
          response.then((res) => {
            setLoading(false)
            refreshTable()
            if (editingUser?.id) {
              refreshTable()
              handleClose()
            } else {
              const inviteLink = `${window.location.origin}/#/invite-register/${(res as { invite_token: string }).invite_token}`
              modal.confirm({
                title: t('member.inviteLinkTip'),
                content: <a href={inviteLink} target="_blank" rel="noopener noreferrer">{inviteLink}</a>,
                okText: t('common.copy'),
                okType: 'danger',
                onOk: () => {
                  copy(inviteLink)
                  handleClose()
                  message.success(t('common.copySuccess'))
                }
              })
            }
          })
          .catch(() => {
            setLoading(false)
          });
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  const labelRender: LabelRender = (props) => {
    const { label, value } = props;

    if (label) {
      return t(`member.${value}`);
    }
    return <span>No option match</span>;
  };

  return (
    <RbModal
      title={t(editingUser ? 'member.editMember' : 'member.createMember')}
      open={visible}
      onCancel={handleClose}
      okText={editingUser ? t('common.save') : t('member.sendInvitation')}
      onOk={handleSave}
      confirmLoading={loading}
    >
      <Form
        form={form}
        initialValues={initialForm}
        layout="vertical"
      >
        <FormItem
          name="email"
          label={t('member.email')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input placeholder={t('common.enterPlaceholder', { title: t('member.email') })} disabled={!!editingUser} />
        </FormItem>
        
        <FormItem
          name="role"
          label={t('member.inviteToMember')}
          rules={[{ required: true, message: t('common.select') }]}
        >
          <Select 
            placeholder={t('common.select')}
            labelRender={labelRender}
          >
            {roleOptions.map(key => (
              <Option key={key} value={key}>
                {t(`member.${key}`)}
                <div className="rb:text-[#5B6167] rb:text-[12px]">{t(`member.${key}Desc`)} </div>
              </Option>
            ))}
          </Select>
        </FormItem>
      </Form>
      {contextHolder}
    </RbModal>
  );
});

export default MemberModal;