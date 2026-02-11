/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:42 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:42 
 */
import { forwardRef, useImperativeHandle, useState } from 'react';
import { Form, Input, App, Transfer, type TransferProps, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { OntologyClassData, ExtractData, OntologyClassExtractModalData, OntologyClassExtractModalRef } from '../types'
import RbModal from '@/components/RbModal'
import { extractOntologyTypes, createOntologyClass } from '@/api/ontology'
import CustomSelect from '@/components/CustomSelect';
import { getModelListUrl } from '@/api/models'
import RbCard from '@/components/RbCard/Card';
import Tag from '@/components/Tag';

const FormItem = Form.Item;

/**
 * Props for OntologyClassExtractModal component
 */
interface OntologyClassExtractModalProps {
  /** Callback function to refresh parent list after extraction */
  refresh: () => void;
}

/**
 * Modal component for extracting ontology classes using LLM
 * Two-step process: 1) Extract classes from scenario 2) Select and confirm classes to add
 */
const OntologyClassExtractModal = forwardRef<OntologyClassExtractModalRef, OntologyClassExtractModalProps>(({
  refresh
}, ref) => {
  // Hooks
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<OntologyClassExtractModalData>();
  
  // State
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<OntologyClassData | null>(null)
  const [extractData, setExtractData] = useState<ExtractData | null>(null)
  const [targetKeys, setTargetKeys] = useState<TransferProps['targetKeys']>([]);
  const [selectedKeys, setSelectedKeys] = useState<TransferProps['selectedKeys']>([]);

  /**
   * Close modal and reset all state
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
    setData(null)
    setExtractData(null)
  };

  /**
   * Open modal with scene data
   * @param vo - Ontology class data containing scene information
   */
  const handleOpen = (vo: OntologyClassData) => {
    form.resetFields();
    setVisible(true);
    setData(vo)
  };
  
  /**
   * Execute LLM extraction to get class suggestions
   */
  const handleSave = () => {
    if (!data?.scene_id) return;
    form
      .validateFields()
      .then((values) => {
        setLoading(true)
        extractOntologyTypes({
          ...values,
          scene_id: data.scene_id,
          domain: data.scene_name,
        }).then((res) => {
          const response = res as ExtractData
          setExtractData(response)
          setSelectedKeys([])
          setTargetKeys(response.classes.map(vo => vo.id))
        })
        .finally(() => {
          setLoading(false)
        })
      })
      .catch((err) => {
        console.log('err', err)
      });
  }

  /**
   * Confirm and create selected classes
   * First click runs extraction, second click creates classes
   */
  const handleConfirm = () => {
    if (!extractData) {
      handleSave()
    } else {
      if (!data?.scene_id) return;
      if (!targetKeys || targetKeys.length === 0) {
        message.warning(t('common.selectPlaceholder', { title: t('ontology.classType') }))
        return
      }
      console.log('targetKeys', targetKeys)
      createOntologyClass({
        scene_id: data?.scene_id,
        classes: extractData.classes.filter(vo => targetKeys?.includes(vo.id)).map(vo => ({ class_name: vo.name, class_description: vo.description }))
      }).then(() => {
        message.success(t('common.createSuccess'))
        refresh()
        handleClose()
      }).finally(() => {
        setLoading(false)
      })
    }
  }

  /**
   * Handle transfer component target keys change
   * @param nextTargetKeys - New target keys after transfer
   */
  const onChange: TransferProps['onChange'] = (nextTargetKeys) => {
    setTargetKeys(nextTargetKeys.filter(Boolean));
  };

  /**
   * Handle transfer component selection change
   * @param sourceSelectedKeys - Selected keys in source list
   * @param targetSelectedKeys - Selected keys in target list
   */
  const onSelectChange: TransferProps['onSelectChange'] = (
    sourceSelectedKeys,
    targetSelectedKeys,
  ) => {
    setSelectedKeys([...sourceSelectedKeys, ...targetSelectedKeys].filter(Boolean));
  };

  /**
   * Expose methods to parent component via ref
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <RbModal
      title={t('ontology.extract')}
      open={visible}
      onCancel={handleClose}
      okText={extractData ? `${t('ontology.extractConfirm')}(${targetKeys?.length})` : loading ? t('ontology.loadingConfirm') : t('ontology.run')}
      onOk={handleConfirm}
      confirmLoading={loading}
      okButtonProps={{ disabled: extractData !== null && targetKeys?.length === 0 }}
      width={1000}
    >
      <Form
        form={form}
        layout="vertical"
      >
        <FormItem
          name="llm_id"
          label={t('ontology.llm_id')}
          rules={[{ required: true, message: t('common.pleaseSelect') }]}
        >
          <CustomSelect
            url={getModelListUrl}
            valueKey="id"
            labelKey="name"
            hasAll={false}
            placeholder={t('common.pleaseSelect')}
            params={{ type: 'llm,chat', pagesize: 100, is_active: true }}
          />
        </FormItem>

        <FormItem
          name="scenario"
          label={t('ontology.scenario')}
          rules={[{ required: true, message: t('common.pleaseEnter') }]}
        >
          <Input.TextArea placeholder={t('ontology.scenarioPlaceholder')} />
        </FormItem>
      </Form>

      {extractData && <RbCard
        title={t('ontology.classType')}
        bodyClassName='rb:flex rb:justify-center rb:h-[450px]!'
      >
        <Transfer
          titles={[t('ontology.source'), t('ontology.target')]}
          dataSource={extractData?.classes?.map(vo => ({ ...vo, key: vo.id }))}
          targetKeys={targetKeys}
          selectedKeys={selectedKeys}
          onChange={onChange}
          onSelectChange={onSelectChange}
          render={(item) => (<div>
            {item.name}
            <Flex wrap gap={8}>{item.examples.map((vo, index) => <Tag color="default" key={index}>{vo}</Tag>)}</Flex>
          </div>)}
          listStyle={{ width: '400px', height: '100%' }}
        />
      </RbCard>}
    </RbModal>
  );
});

export default OntologyClassExtractModal;