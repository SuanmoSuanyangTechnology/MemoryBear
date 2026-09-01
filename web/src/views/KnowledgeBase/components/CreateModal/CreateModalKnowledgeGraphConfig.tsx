import { Button, Flex, Form, Input, Radio } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import SwitchFormItem from '@/components/FormItem/SwitchFormItem';

const { TextArea } = Input;

interface KnowledgeGraphSwitchFormItemProps {
  name: string[];
  title: string;
  desc: string;
  checked: boolean;
}

const KnowledgeGraphSwitchFormItem = ({
  name,
  title,
  desc,
  checked,
}: KnowledgeGraphSwitchFormItemProps) => (
  <SwitchFormItem
    name={name}
    title={title}
    desc={desc}
    className={clsx(
      'rb:w-full rb:p-4! rb:border rb:rounded-lg rb:mb-4!',
      {
        'rb:border-[#155EEF] rb:bg-[rgba(21,94,239,0.06)]': checked,
        'rb:border-[#EBEBEB]': !checked,
      }
    )}
  />
);

interface CreateModalKnowledgeGraphConfigProps {
  generatingEntityTypes: boolean;
  onGenerateEntityTypes: () => void;
}

const CreateModalKnowledgeGraphConfig = ({
  generatingEntityTypes,
  onGenerateEntityTypes,
}: CreateModalKnowledgeGraphConfigProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const {
    use_graphrag = false,
    entity_types = '',
    resolution = false,
    community = false,
  } = Form.useWatch(['parser_config', 'graphrag'], form) || {};

  return (
    <>
      <KnowledgeGraphSwitchFormItem
        name={['parser_config', 'graphrag', 'use_graphrag']}
        title={t('knowledgeBase.enableKnowledgeGraph')}
        desc={t('knowledgeBase.enableKnowledgeGraphTips')}
        checked={use_graphrag}
      />

      {use_graphrag && (
        <>
          <div className="rb:text-[#212332] rb:text-base rb:font-medium rb:mb-4">
            {t('knowledgeBase.graphConfig')}
          </div>
          <Flex align="center" gap={8}>
            <Form.Item
              name={['parser_config', 'graphrag', 'scene_name']}
              label={t('knowledgeBase.sceneName')}
              className="rb:w-full rb:min-w-60"
              rules={[{ required: true, message: t('common.pleaseEnter') + t('knowledgeBase.sceneName') }]}
            >
              <Input placeholder={t('knowledgeBase.sceneNamePlaceholder')} />
            </Form.Item>
            <Button
              type="primary"
              loading={generatingEntityTypes}
              onClick={onGenerateEntityTypes}
              className="rb:mt-3"
            >
              {!entity_types || entity_types.trim() === ''
                ? t('knowledgeBase.generateEntityTypes')
                : t('knowledgeBase.regenerateEntityTypes')}
            </Button>
          </Flex>

          <Form.Item
            name={['parser_config', 'graphrag', 'entity_types']}
            label={t('knowledgeBase.entityTypes')}
          >
            <TextArea rows={4} placeholder={t('knowledgeBase.entityTypesPlaceholder')} />
          </Form.Item>

          <KnowledgeGraphSwitchFormItem
            name={['parser_config', 'graphrag', 'resolution']}
            title={t('knowledgeBase.entityNormalization')}
            desc={t('knowledgeBase.entityNormalizationTips')}
            checked={resolution}
          />

          <Form.Item
            name={['parser_config', 'graphrag', 'method']}
            label={t('knowledgeBase.entityMethod')}
            initialValue="general"
          >
            <Radio.Group>
              <Radio value="general">{t('knowledgeBase.entityMethodGeneral')}</Radio>
              <Radio value="light">{t('knowledgeBase.entityMethodLight')}</Radio>
            </Radio.Group>
          </Form.Item>

          <KnowledgeGraphSwitchFormItem
            name={['parser_config', 'graphrag', 'community']}
            title={t('knowledgeBase.communityReportGeneration')}
            desc={t('knowledgeBase.communityReportGenerationTips')}
            checked={community}
          />
        </>
      )}
    </>
  );
};

export default CreateModalKnowledgeGraphConfig;
