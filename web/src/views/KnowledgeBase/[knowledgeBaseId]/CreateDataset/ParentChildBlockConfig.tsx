import type { FC } from 'react';
import { Col, Flex, Form, InputNumber, Radio, Row } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import DelimiterSelector from '../../components/DelimiterSelector';

const { Item: FormItem } = Form;

export interface ParentChildBlockConfigValues {
  parent_chunk_mode: 'paragraph' | 'full-doc';
  parent_chunk_delimiter: string;
  parent_chunk_token_num: number;
  delimiter: string;
  chunk_token_num: number;
}

export const parentChildBlockConfigValues: ParentChildBlockConfigValues = {
  parent_chunk_mode: 'paragraph',
  parent_chunk_delimiter: '\n\n',
  parent_chunk_token_num: 1024,
  delimiter: '\n',
  chunk_token_num: 512,
};

const ParentChildBlockConfig: FC = () => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<ParentChildBlockConfigValues>();
  const parentChunkMode = Form.useWatch('parent_chunk_mode', form) ?? 'paragraph';

  const selectMode = (mode: ParentChildBlockConfigValues['parent_chunk_mode']) => {
    form.setFieldValue('parent_chunk_mode', mode);
  };

  return (
    <div className="rb-border rb:rounded-xl rb:py-3 rb:px-6 rb:mt-5">
      <div className="rb:font-medium rb:text-[#171719]">{t('knowledgeBase.parentChildSegmentation')}</div>
      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-4">{t('knowledgeBase.parentChildDescription')}</div>

      <div className="rb:mb-6">
        <div className="rb:font-medium rb:text-[#171719] rb:mb-3">{t('knowledgeBase.parentBlockAsContext')}</div>
        <Flex vertical gap={12}>
          <Flex
            align="center"
            gap={16}
            className={clsx('rb:cursor-pointer rb:p-4! rb:border rb:rounded-xl rb:transition-all', {
              'rb:border-[#171719] rb:bg-[#FAFAFA]': parentChunkMode === 'paragraph',
              'rb:border-[#E5E5E5]': parentChunkMode !== 'paragraph',
            })}
            onClick={() => selectMode('paragraph')}
          >
            <Radio checked={parentChunkMode === 'paragraph'} />
            <div className="rb:flex-1">
              <div className="rb:font-medium rb:mb-1">{t('knowledgeBase.paragraph')}</div>
              <p className={clsx('rb:text-[12px] rb:text-[#5B6167]', { 'rb:mb-3': parentChunkMode === 'paragraph' })}>
                {t('knowledgeBase.paragraphDescription')}
              </p>
              {parentChunkMode === 'paragraph' && (
                <Row gutter={16}>
                  <Col span={12}>
                    <FormItem name="parent_chunk_delimiter" label={t('knowledgeBase.segmentDelimiter')}>
                      <DelimiterSelector />
                    </FormItem>
                  </Col>
                  <Col span={12}>
                    <FormItem name="parent_chunk_token_num" label={t('knowledgeBase.maxSegmentLength')}>
                      <InputNumber min={1} placeholder={t('common.pleaseEnter')} suffix="characters" className="rb:w-full!" />
                    </FormItem>
                  </Col>
                </Row>
              )}
            </div>
          </Flex>

          <Flex
            align="center"
            gap={16}
            className={clsx('rb:cursor-pointer rb:p-4! rb:border rb:rounded-xl rb:transition-all', {
              'rb:border-[#171719] rb:bg-[#FAFAFA]': parentChunkMode === 'full-doc',
              'rb:border-[#E5E5E5]': parentChunkMode !== 'full-doc',
            })}
            onClick={() => selectMode('full-doc')}
          >
            <Radio checked={parentChunkMode === 'full-doc'} />
            <div className="rb:flex-1">
              <div className="rb:font-medium rb:mb-1">{t('knowledgeBase.full-doc')}</div>
              <p className="rb:text-[12px] rb:text-[#5B6167]">{t('knowledgeBase.fullTextDescription')}</p>
            </div>
          </Flex>
        </Flex>
        <Form.Item name="parent_chunk_mode" hidden />
      </div>

      <div>
        <div className="rb:font-medium rb:text-[#171719] rb:mb-3">{t('knowledgeBase.childBlockForRetrieval')}</div>
        <Row gutter={16}>
          <Col span={12}>
            <FormItem name="delimiter" label={t('knowledgeBase.segmentDelimiter')}>
              <DelimiterSelector />
            </FormItem>
          </Col>
          <Col span={12}>
            <FormItem name="chunk_token_num" label={t('knowledgeBase.maxSegmentLength')}>
              <InputNumber min={1} placeholder={t('common.pleaseEnter')} suffix="characters" className="rb:w-full!" />
            </FormItem>
          </Col>
        </Row>
      </div>
    </div>
  );
};

export default ParentChildBlockConfig;
