import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Cascader, App } from 'antd';
import { useTranslation } from 'react-i18next';

import type { SubModelModalForm, SubModelModalRef, SubModelModalProps, ModelList } from './types';
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
  type
}, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<SubModelModalForm>();
  const [selecteds, setSelecteds] = useState<any[]>([])
  const [modelList, setModelList] = useState<Option[]>([])

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    form.resetFields();
    setVisible(false);
    setSelecteds([])
  };

  const handleOpen = (list?: ModelList[], provider?: string) => {
    if (list?.length && provider) {
      const initialValue: SubModelModalForm = {
        provider,
        api_key_ids: list.map(vo => {
          return [vo.model_config_ids[0], vo.id]
        })
      }

      form.setFieldsValue(initialValue);
      handleChangeProvider(provider, initialValue.api_key_ids)
    } else {
      form.resetFields()
    }
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
          id: vo[1].value
        })))
        handleClose()
      })
  }
  const handleChange = (value: (string | number)[][], selectedOptions: Option[][]) => {
    const filterList = selectedOptions.filter(vo => vo.length === 1).map(item => item[0])
    const lastFilterLit = value.filter(vo => vo.length !== 1)
    console.log('onchange', value, lastFilterLit, selectedOptions, filterList)
    if (filterList.length) {
      message.warning(`【${filterList.map(vo => vo.label)}】${t('modelNew.selectOneTip')}`)
      form.setFieldValue('api_key_ids', lastFilterLit)
    }
    setSelecteds(selectedOptions)
  }

  const handleChangeProvider = (provider: string, api_key_ids?: any[]) => {
    form.setFieldValue('api_key_ids', undefined)
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
  }

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
          />
        </Form.Item>
      </Form>
    </RbModal>
  );
});

export default SubModelModal;