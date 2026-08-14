import { Checkbox, Flex, Form, Input, Popover, Radio, Select } from 'antd';
import type { FormInstance } from 'antd';
import { useTranslation } from 'react-i18next';
import SliderInput from '@/components/SliderInput';
import DelimiterSelector from '../../components/DelimiterSelector';
import ParentChildBlockConfig from './ParentChildBlockConfig';
import type { CreateDatasetFormValues, ProcessingMethod } from './types';
import RadioGroupCard from '@/components/RadioGroupCard';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem';

const groupStyle: React.CSSProperties = { display: 'flex', gap: 16 };
const radioBaseStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'flex-start',
  columnGap: 14,
  width: '100%',
  border: '1px solid #E5E5E5',
  borderRadius: 12,
  padding: 16,
};
const activeRadioStyle = (active: boolean): React.CSSProperties => ({
  ...radioBaseStyle,
  border: active ? '1px solid #171719' : radioBaseStyle.border,
  backgroundColor: active ? '#FAFAFA' : 'transparent',
});

const typeTipImageMap: Record<'textImage' | 'hybridImage' | 'pureImage', string> = {
  textImage: "rb:bg-[url('@/assets/images/knowledgeBase/textImage.png')]",
  hybridImage: "rb:bg-[url('@/assets/images/knowledgeBase/hybridImage.png')]",
  pureImage: "rb:bg-[url('@/assets/images/knowledgeBase/pureImage.png')]",
};

interface ParameterStepProps {
  form: FormInstance<CreateDatasetFormValues>;
  fileIds: string[];
  isParentChildMode: boolean | null;
}

const ParameterStep = ({ form, fileIds, isParentChildMode }: ParameterStepProps) => {
  const { t } = useTranslation();
  const {
    processingMethod = 'directBlock',
    parameterSettings = 'defaultSettings',
    pdfEnhancementEnabled = true,
    blockSize = 512,
    image,
  } = Form.useWatch([], form) || {};

  const changeProcessingMethod = (method: ProcessingMethod) => {
    form.setFieldValue('processingMethod', method);
    if (method === 'directBlock') {
      form.setFieldsValue({ blockSize: 512, chunkOverlap: 52 });
    }
  };

  const changeBlockSize = (value: number | null) => {
    if (value === null) return;
    const overlap = form.getFieldValue('chunkOverlap');
    if (processingMethod === 'directBlock' && overlap >= value) {
      form.setFieldValue('chunkOverlap', Math.max(1, value - 1));
    }
  };

  console.log('parameterSettings', processingMethod, parameterSettings, pdfEnhancementEnabled, blockSize)

  return (
    <Flex vertical className="rb:mt-10! rb:px-40!">
      {fileIds.length > 0 && (
        <Flex align="center" wrap gap={8} className="rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-3! rb:py-2! rb:mb-4! rb:text-xs rb:text-gray-600">
          <span className="rb:text-gray-700 rb:font-medium">{t('knowledgeBase.rechunking')}:</span>
          {fileIds.map((id) => <span key={id} className="rb:px-2 rb:py-0.5 rb:bg-white rb:border rb:border-[#DFE4ED] rb:rounded">{id}</span>)}
        </Flex>
      )}

      <SwitchFormItem
        title={t('knowledgeBase.imageParsingSettings')}
        name={['image', 'vision_enabled']}
        desc={t('knowledgeBase.imageParsingSettingsDesc')}
      />

      {image?.vision_enabled &&
        <Form.Item name={['image', 'vision_mode']} className="rb:mt-3!"
          getValueProps={(value) => ({ value: String(value) })}
          getValueFromEvent={(value) => Number(value)}
        >
          <RadioGroupCard
            options={(['textImage', 'hybridImage', 'pureImage'] as const).map((type, index) => ({
              value: String(index),
              label: t(`knowledgeBase.${type}`),
              labelDesc: t(`knowledgeBase.${type}Desc`),
              type,
            }))}
            itemRender={(option) => {
              const type = option.type as 'textImage' | 'hybridImage' | 'pureImage';
              return (
                <Flex gap={12} align="center" justify="center" className="rb:items-start! rb:text-left!">
                  <div className="rb:flex rb:flex-col rb:gap-2">
                    <Flex align="center" gap={4} className="rb:font-medium rb:text-[#212332]">
                      <span>{option.label}</span>
                      <Popover
                        content={
                          <Flex align="start" gap={12} className="rb:w-64!">
                            <div className="rb:shrink-0 rb:size-24 rb:overflow-hidden rb:rounded-lg rb-border">
                              <div className={`rb:size-full rb:bg-cover ${typeTipImageMap[type]}`} />
                            </div>
                            <div>
                              <div className="rb:text-sm rb:font-semibold rb:text-[#171719] rb:mb-2">
                                {t(`knowledgeBase.${type}TipsTitle`)}
                              </div>
                              <div className="rb:text-xs rb:text-[#5B6167] rb:leading-5 rb:mb-3">
                                {t(`knowledgeBase.${type}TipsDesc`)}
                              </div>
                              <div className="rb:text-xs rb:text-[#8A8F99]">
                                {t(`knowledgeBase.${type}TipsSample`)}
                              </div>
                            </div>
                          </Flex>
                        }
                        placement="top"
                        trigger="hover"
                      >
                        <div className="rb:size-4 rb:cursor-help rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
                      </Popover>
                    </Flex>
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5">
                      {option.labelDesc}
                    </div>
                  </div>
                </Flex>
              );
            }}
          />
        </Form.Item>
      }

      <div className="rb:text-base rb:font-medium rb:text-gray-800 rb:mt-4">{t('knowledgeBase.fileParsingSettings')}</div>
      <Flex
        align="center"
        justify="space-between"
        className={`rb:w-full rb:border rb:rounded-xl rb:p-4! rb:mt-4! ${pdfEnhancementEnabled ? 'rb:border-[#171719] rb:bg-[#FAFAFA]' : 'rb-border'}`}
      >
        <Form.Item name="pdfEnhancementEnabled" valuePropName="checked" noStyle>
          <Checkbox className="rb:mr-3">
            <span className="rb:text-base rb:font-medium rb:text-gray-800 rb:pl-5.5">{t('knowledgeBase.pdfEnhancementAnalysis')}</span>
          </Checkbox>
        </Form.Item>
        {pdfEnhancementEnabled && (
          <Form.Item name="pdfEnhancementMethod" noStyle>
            <Select className="rb:w-75!" options={[
              { value: 'deepdoc', label: 'DeepDoc' },
              { value: 'mineru', label: 'MinerU' },
              { value: 'textln', label: 'TextLN' },
            ]} />
          </Form.Item>
        )}
      </Flex>

      <div className="rb:text-base rb:font-medium rb:text-gray-800 rb:mt-6">{t('knowledgeBase.dataProcessingSettings')}</div>
      <div className="rb:font-medium rb:text-gray-500 rb:mt-4 rb:mb-3">{t('knowledgeBase.processingMethod')}</div>
      <Form.Item name="processingMethod" noStyle>
        <Radio.Group
          style={groupStyle}
          onChange={(event) => changeProcessingMethod(event.target.value as ProcessingMethod)}
        >
          <Radio value="directBlock" disabled={isParentChildMode === true} style={activeRadioStyle(processingMethod === 'directBlock')}>
            <span className="rb:text-base rb:font-medium rb:text-gray-800">{t('knowledgeBase.directBlock')}</span>
          </Radio>
          <Radio value="qaExtract" disabled={isParentChildMode === true} style={activeRadioStyle(processingMethod === 'qaExtract')}>
            <span className="rb:text-base rb:font-medium rb:text-gray-800">{t('knowledgeBase.qaExtract')}</span>
          </Radio>
          <Radio value="parentChildBlock" disabled={isParentChildMode === false} style={activeRadioStyle(processingMethod === 'parentChildBlock')}>
            <span className="rb:text-base rb:font-medium rb:text-gray-800">{t('knowledgeBase.parentChildBlock')}</span>
          </Radio>
        </Radio.Group>
      </Form.Item>

      <div className="rb:font-medium rb:text-gray-500 rb:mt-4 rb:mb-3">{t('knowledgeBase.parameterSettings')}</div>
      <Form.Item name="parameterSettings" noStyle>
        <Radio.Group
          style={groupStyle}
        >
          <Radio value="defaultSettings" style={activeRadioStyle(parameterSettings === 'defaultSettings')}>
            <Flex gap="small" vertical>
              <span className="rb:text-base rb:font-medium rb:text-gray-800">{t('knowledgeBase.default')}</span>
              <span className="rb:text-3 rb:text-gray-500">{t('knowledgeBase.defaultSettings')}</span>
            </Flex>
          </Radio>
          <Radio value="customSettings" style={activeRadioStyle(parameterSettings === 'customSettings')}>
            <Flex gap="small" vertical>
              <span className="rb:text-base rb:font-medium rb:text-gray-800">{t('knowledgeBase.customize')}</span>
              <span className="rb:text-3 rb:text-gray-500">{t('knowledgeBase.customSettings')}</span>
            </Flex>
          </Radio>
        </Radio.Group>
      </Form.Item>

      <div className={parameterSettings === 'customSettings' && processingMethod !== 'parentChildBlock' ? 'rb:block' : 'rb:hidden'}>
        <div className="rb:grid rb:grid-cols-3 rb:mt-5 rb-border rb:rounded-xl rb:px-6 rb:py-4 rb:gap-10">
          <div>
            <div className="rb:w-full rb:text-[#5B6167] rb:leading-5 rb:mb-2">{t('knowledgeBase.delimiter')}</div>
            {parameterSettings === 'customSettings' && processingMethod !== 'parentChildBlock' && <Form.Item name="delimiter" noStyle><DelimiterSelector /></Form.Item>}
          </div>
          <Form.Item name="blockSize" noStyle>
            <SliderInput label={t('knowledgeBase.suggestedBlockSize')} max={1024} min={processingMethod === 'directBlock' ? 2 : 1} step={1} onChange={changeBlockSize} />
          </Form.Item>
          {processingMethod === 'directBlock' && (
            <Form.Item name="chunkOverlap" noStyle>
              <SliderInput
                label={<span><span className="rb:text-[#ff5d34] rb:mr-1">*</span>{t('knowledgeBase.chunkOverlap')}</span>}
                max={blockSize - 1}
                min={1}
                step={1}
              />
            </Form.Item>
          )}
        </div>
        {processingMethod === 'qaExtract' && (
          <Form.Item name="qaPrompt" label={t('knowledgeBase.qaPrompt')} className="rb:mt-4!">
            <Input.TextArea rows={6} />
          </Form.Item>
        )}
      </div>
      {parameterSettings === 'customSettings' && processingMethod === 'parentChildBlock' && <ParentChildBlockConfig />}
    </Flex>
  );
};

export default ParameterStep;
