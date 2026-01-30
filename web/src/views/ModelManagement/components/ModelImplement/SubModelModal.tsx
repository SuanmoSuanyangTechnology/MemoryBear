import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Cascader, App, type CascaderProps } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SubModelModalForm, SubModelModalRef, SubModelModalProps } from './types';
import RbModal from '@/components/RbModal'
import CustomSelect from '@/components/CustomSelect'
import { modelProviderUrl, getModelNewList } from '@/api/models'
import type { ProviderModelItem } from '../../types'

const { SHOW_CHILD } = Cascader;

interface Option {
  value: string | number;
  label: string;
  children?: Option[];
  [key: string]: any;
}
const SubModelModal = forwardRef<SubModelModalRef, SubModelModalProps>(({
  refresh,
  type,
  groupedByProvider
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SubModelModalForm>();
  const [selecteds, setSelecteds] = useState<any[]>([])
  const [modelList, setModelList] = useState<Option[]>([])
  const provider = Form.useWatch(['provider'], form)

  useEffect(() => {
    if (provider && groupedByProvider) {
      const lastModels = groupedByProvider[provider] || []
      const list = lastModels.map(vo => [{ name: vo.model_name, id: vo.model_config_ids[0], value: vo.model_config_ids[0], provider }, { value: vo.id }])
      setSelecteds(list)
      form.setFieldValue('api_key_ids', lastModels.map(vo => [vo.model_config_ids[0], vo.id]))
    }
  }, [groupedByProvider, provider])

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    form.resetFields();
    setVisible(false);
    setSelecteds([])
    setModelList([])
  };

  const handleOpen = () => {
    form.resetFields()
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    form
      .validateFields()
      .then(() => {
        refresh?.(selecteds.map(vo => ({
          ...vo[0],
          model_name: vo[0].name,
          model_config_ids: [vo[0].id],
          id: vo[1].value,
          api_key: vo[1].label
        })))
        handleClose()
      })
  }
  const handleChange = (value: (string | number)[][], selectedOptions: Option[][]) => {
    const filterList = selectedOptions.filter(vo => vo.length === 1).map(item => item[0])
    const lastFilterLit = value.filter(vo => vo.length !== 1)
    if (filterList.length) {
      message.warning(`【${filterList.map(vo => vo.label)}】${t('modelNew.selectOneTip')}`)
      form.setFieldValue('api_key_ids', lastFilterLit)
    }
    setSelecteds(selectedOptions)
  }

  const handleChangeProvider = (provider: string, api_key_ids?: any[]) => {
    form.setFieldValue('api_key_ids', undefined)
    if (provider) {
      getModelNewList({
        provider: provider,
        is_composite: false,
        is_active: true,
        type
      })
        .then(res => {
          const response = res as ProviderModelItem[]
          const list = response[0]?.models || []
          setModelList(list.map(vo => {
            const children = vo.api_keys.map(item => ({
              label: item.api_key,
              value: item.id,
            }))
            return {
              ...vo,
              label: vo.name,
              value: vo.id,
              children: children
            }
          }))

          if (api_key_ids?.length) {
            form.setFieldsValue({
              api_key_ids: api_key_ids
            })
          }
        })
    } else {
      setModelList([])
    }
  }
  const displayRender: CascaderProps<Option>['displayRender'] = (labels, selectedOptions = []) =>
    labels.map((label, i) => {
      const option = selectedOptions[i];
      if (i === labels.length - 1) {
        return (
          <span key={option?.value || i}>
            {label}
          </span>
        );
      }
      return <span key={option?.value || i}>{label} / </span>;
    });

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('modelNew.implementConfig')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <Form.Item
          name="provider"
          label={t('modelNew.provider')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.provider') }) }]}
        >
          <CustomSelect
            placeholder={t('common.pleaseSelect')}
            url={modelProviderUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ 
              label: t(`modelNew.${typeof item === 'object' ? item.value : item}`), 
              value: typeof item === 'object' ? item.value : item 
            }))}
            onChange={(value) => handleChangeProvider(value)}
          />
        </Form.Item>
        <Form.Item 
          name="api_key_ids"
          label={t('modelNew.api_key_ids')}
          rules={[{ required: true, message: t('common.selectPlaceholder', { title: t('modelNew.api_key_ids') }) }]}
        >
          <Cascader
            placeholder={t('common.pleaseSelect')}
            options={modelList}
            onChange={handleChange}
            multiple
            autoClearSearchValue
            className="rb:w-full!"
            showCheckedStrategy={SHOW_CHILD}
            changeOnSelect
            displayRender={displayRender}
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default SubModelModal;