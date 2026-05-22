import { type FC, useState, useEffect } from 'react';
import { Form, Input, InputNumber, Radio, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';


import DelimiterSelector from './DelimiterSelector';
const { Item: FormItem } = Form;

export interface ParentChildBlockConfigValues {
  parent_chunk_mode: 'paragraph' | 'full-doc';
  parent_chunk_delimiter: string;
  parent_chunk_token_num: number;
  delimiter: string;
  chunk_token_num: number;
  // replaceWhitespace: boolean;
  // removeUrls: boolean;
  // removeEmails: boolean;
}

export const parentChildBlockConfigValues: ParentChildBlockConfigValues = {
  parent_chunk_mode: 'paragraph',
  parent_chunk_delimiter: '\n\n',
  parent_chunk_token_num: 1024,
  delimiter: '\n',
  chunk_token_num: 512,
  // replaceWhitespace: true,
  // removeUrls: false,
  // removeEmails: false,
};
interface ParentChildBlockConfigProps {
  initialValue?: ParentChildBlockConfigValues;
  onChange?: (values: ParentChildBlockConfigValues) => void;
}

const ParentChildBlockConfig: FC<ParentChildBlockConfigProps> = ({ initialValue, onChange }) => {
  const { t } = useTranslation();
  const [form] = Form.useForm<ParentChildBlockConfigValues>();
  const formValues = Form.useWatch([], form)

  console.log('formValues', formValues)

  const [parent_chunk_mode, setParentBlockType] = useState<'paragraph' | 'full-doc'>('paragraph');

  const handleParentBlockTypeChange = (value: 'paragraph' | 'full-doc') => {
    setParentBlockType(value);
    form.setFieldValue('parent_chunk_mode', value);
    const values = form.getFieldsValue();
    onChange?.({...values, parent_chunk_mode: value});
  };

  const onValuesChange = (_changedValues: ParentChildBlockConfigValues, allValues: ParentChildBlockConfigValues) => {
    onChange?.(allValues as ParentChildBlockConfigValues);
  }

  useEffect(() => {
    const defaultValues = parentChildBlockConfigValues;
    const values = initialValue 
      ? { ...defaultValues, ...initialValue } 
      : defaultValues;
    // form.resetFields();
    console.log('values', values)
    form.setFieldsValue(values);
    setParentBlockType(values.parent_chunk_mode);
  }, [form]);

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={initialValue || parentChildBlockConfigValues}
      onValuesChange={onValuesChange}
    >
      <div className="rb-border rb:rounded-xl rb:py-3 rb:px-6 rb:mt-5">
        <div className="rb:font-medium rb:text-[#171719]">{t('knowledgeBase.parentChildSegmentation')}</div>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-4">{t('knowledgeBase.parentChildDescription')}</div>

        <div className="rb:mb-6">
          <div className="rb:font-medium rb:text-[#171719] rb:mb-3">{t('knowledgeBase.parentBlockAsContext')}</div>
          
          <div className="rb:space-y-3">
            <div
              className={clsx("rb:flex rb:items-center rb:cursor-pointer rb:gap-4 rb:p-4 rb:border rb:rounded-xl cursor-pointer transition-all", {
                'rb:border-[#171719] rb:bg-[#FAFAFA]': parent_chunk_mode === 'paragraph',
                'rb:border-[#E5E5E5]': parent_chunk_mode !== 'paragraph',
              })}
              onClick={() => handleParentBlockTypeChange('paragraph')}
            >
              <Radio 
                checked={parent_chunk_mode === 'paragraph'} 
                onChange={() => handleParentBlockTypeChange('paragraph')} 
              />
              <div className="rb:flex-1">
                <div className="rb:font-medium rb:mb-1">{t('knowledgeBase.paragraph')}</div>
                <p className={clsx("rb:text-[12px] rb:text-[#5B6167]", {
                  'rb:mb-3': parent_chunk_mode === 'paragraph',
                })}>{t('knowledgeBase.paragraphDescription')}</p>
                <Row gutter={16}>
                  <Col span={12}>
                    <FormItem
                      name="parent_chunk_delimiter"
                      label={t('knowledgeBase.segmentDelimiter')}
                      hidden={parent_chunk_mode !== 'paragraph'}
                    >
                      <DelimiterSelector  />
                    </FormItem>
                  </Col>
                  <Col span={12}>
                    <FormItem
                      name="parent_chunk_token_num"
                      label={t('knowledgeBase.maxSegmentLength')}
                      hidden={parent_chunk_mode !== 'paragraph'}
                    >
                      <InputNumber
                        placeholder={t('common.pleaseEnter')}
                        suffix="characters"
                        className="rb:w-full!"
                      />
                    </FormItem>
                  </Col>
                </Row>
              </div>
            </div>

            <div
              className={clsx("rb:flex rb:items-center rb:cursor-pointer rb:gap-4 rb:p-4 rb:border rb:rounded-xl cursor-pointer transition-all", {
                'rb:border-[#171719] rb:bg-[#FAFAFA]': parent_chunk_mode === 'full-doc',
                'rb:border-[#E5E5E5]': parent_chunk_mode !== 'full-doc',
              })}
              onClick={() => handleParentBlockTypeChange('full-doc')}
            >
              <Radio 
                checked={parent_chunk_mode === 'full-doc'} 
                onChange={() => handleParentBlockTypeChange('full-doc')} 
              />
              <div className="rb:flex-1">
                <div className="rb:font-medium rb:mb-1">{t('knowledgeBase.full-doc')}</div>
                <p className="rb:text-[12px] rb:text-[#5B6167]">{t('knowledgeBase.fullTextDescription')}</p>
              </div>
            </div>
          </div>
          <Form.Item name="parent_chunk_mode" hidden />
        </div>

        <div className="rb:mb-6">
          <div className="rb:font-medium rb:text-[#171719] rb:mb-3">{t('knowledgeBase.childBlockForRetrieval')}</div>
          <Row gutter={16}>
            <Col span={12}>
              <FormItem
                name="delimiter"
                label={t('knowledgeBase.segmentDelimiter')}
              >
                <DelimiterSelector />
              </FormItem>
            </Col>
            <Col span={12}>
              <FormItem
                name="chunk_token_num"
                label={t('knowledgeBase.maxSegmentLength')}
              >
                <InputNumber
                  placeholder={t('common.pleaseEnter')}
                  suffix="characters"
                  className="rb:w-full!"
                />
              </FormItem>
            </Col>
          </Row>
        </div>

        {/* <>
          <div className="rb:font-medium rb:text-[#171719] rb:mb-3">{t('knowledgeBase.textPreprocessingRules')}</div>
          <div className="rb:space-y-2">
            <FormItem
              name="replaceWhitespace"
              valuePropName="checked"
              className="rb:mb-0!"
            >
              <Checkbox>
                {t('knowledgeBase.replaceWhitespace')}
              </Checkbox>
            </FormItem>
            <FormItem
              name="removeEmails"
              valuePropName="checked"
              className="rb:mb-0!"
            >
              <Checkbox>
                {t('knowledgeBase.removeEmails')}
              </Checkbox>
            </FormItem>
          </div>
        </> */}
      </div>
    </Form>
  );
};

export default ParentChildBlockConfig;