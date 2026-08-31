import { Form, Input, Select } from 'antd';
import { useTranslation } from 'react-i18next';

import ModelSelect from '@/components/ModelSelect';
import SliderInput from '@/components/SliderInput';
import { stringRegExp } from '@/utils/validator';
import type { KnowledgeBaseFormData } from '@/views/KnowledgeBase/types';
import type { Model } from '@/views/ModelManagement/types';
import { MODEL_TYPE_CONFIG } from './useCreateModalModels';

const { TextArea } = Input;

type KnowledgeBaseType = 'General' | 'Web' | 'Third-party' | 'Folder';

interface CreateModalBasicConfigProps {
  isEditing: boolean;
  currentType: KnowledgeBaseType;
  dynamicTypeList: string[];
  customModels: Record<string, Model[]>;
  onModelChange: (value: string, type: string) => void;
}

const CreateModalBasicConfig = ({
  isEditing,
  currentType,
  dynamicTypeList,
  customModels,
  onModelChange,
}: CreateModalBasicConfigProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<KnowledgeBaseFormData>();
  const thirdPartyPlatform = Form.useWatch(['parser_config', '_third_party_platform'], form) || 'yuque';

  return (
    <>
      {!isEditing && (
        <Form.Item
          name="name"
          label={t('knowledgeBase.createForm.name')}
          rules={[
            { required: true, message: t('knowledgeBase.createForm.nameRequired') },
            { max: 50 },
            { pattern: stringRegExp, message: t('common.nameInvalid') },
          ]}
        >
          <Input placeholder={t('knowledgeBase.createForm.name')} />
        </Form.Item>
      )}
      <Form.Item name="description" label={t('knowledgeBase.createForm.description')} rules={[{ max: 500 }]}>
        <TextArea rows={2} placeholder={t('knowledgeBase.createForm.description')} />
      </Form.Item>

      {currentType === 'Web' && (
        <>
          <Form.Item
            name={['parser_config', 'entry_url']}
            label={t('knowledgeBase.createForm.entryUrl')}
            rules={[
              { required: true, message: t('knowledgeBase.createForm.entryUrlRequired') },
              { type: 'url', message: t('knowledgeBase.createForm.entryUrlInvalid') },
            ]}
          >
            <Input placeholder="https://ai.redbearai.com" disabled={isEditing} />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'max_pages']}
            label={t('knowledgeBase.createForm.maxPages')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.maxPagesRequired') }]}
            initialValue={20}
          >
            <SliderInput min={10} max={200} step={10} disabled={isEditing} />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'delay_seconds']}
            label={t('knowledgeBase.createForm.delaySeconds')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.delaySecondsRequired') }]}
            initialValue={2}
          >
            <SliderInput min={1} max={3} step={1} disabled={isEditing} />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'timeout_seconds']}
            label={t('knowledgeBase.createForm.timeoutSeconds')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.timeoutSecondsRequired') }]}
            initialValue={10}
          >
            <SliderInput min={5} max={15} step={1} disabled={isEditing} />
          </Form.Item>

          <Form.Item
            name={['parser_config', 'user_agent']}
            label={t('knowledgeBase.createForm.userAgent')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.userAgentRequired') }]}
            initialValue="KnowledgeBaseCrawler/1.0"
          >
            <Input placeholder="KnowledgeBaseCrawler/1.0" disabled={isEditing} />
          </Form.Item>
        </>
      )}

      {currentType === 'Third-party' && (
        <>
          <Form.Item
            name={['parser_config', '_third_party_platform']}
            label={t('knowledgeBase.createForm.platform')}
            rules={[{ required: true, message: t('knowledgeBase.createForm.platformRequired') }]}
            initialValue="yuque"
          >
            <Select
              disabled={isEditing}
              options={[
                { value: 'yuque', label: t('knowledgeBase.createForm.yuque') },
                { value: 'feishu', label: t('knowledgeBase.createForm.feishu') },
              ]}
            />
          </Form.Item>

          {thirdPartyPlatform === 'yuque' && (
            <>
              <Form.Item
                name={['parser_config', 'yuque_user_id']}
                label={t('knowledgeBase.createForm.yuqueUserId')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.yuqueUserIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.yuqueUserIdPlaceholder')} disabled={isEditing} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'yuque_token']}
                label={t('knowledgeBase.createForm.yuqueToken')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.yuqueTokenRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createForm.yuqueTokenPlaceholder')} disabled={isEditing} />
              </Form.Item>
            </>
          )}

          {thirdPartyPlatform === 'feishu' && (
            <>
              <Form.Item
                name={['parser_config', 'feishu_app_id']}
                label={t('knowledgeBase.createForm.feishuAppId')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuAppIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.feishuAppIdPlaceholder')} disabled={isEditing} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'feishu_app_secret']}
                label={t('knowledgeBase.createForm.feishuAppSecret')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuAppSecretRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createForm.feishuAppSecretPlaceholder')} disabled={isEditing} />
              </Form.Item>

              <Form.Item
                name={['parser_config', 'feishu_folder_token']}
                label={t('knowledgeBase.createForm.feishuFolderToken')}
                rules={[{ required: true, message: t('knowledgeBase.createForm.feishuFolderTokenRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createForm.feishuFolderTokenPlaceholder')} disabled={isEditing} />
              </Form.Item>
            </>
          )}
        </>
      )}

      {currentType !== 'Folder' && dynamicTypeList.map((type) => {
        const normalizedType = (type || '').toLowerCase();
        const modelTypeConfig = MODEL_TYPE_CONFIG[normalizedType];
        const fieldKey = modelTypeConfig?.fieldKey || `${normalizedType}_id`;
        const options = customModels[modelTypeConfig?.modelType || type] || [];

        return (
          <Form.Item
            key={type}
            name={fieldKey as keyof KnowledgeBaseFormData}
            label={`${t(`knowledgeBase.createForm.${fieldKey}`)} model`}
            rules={[{ required: true, message: t('knowledgeBase.createForm.modelRequired') }]}
          >
            <ModelSelect
              placeholder={t(`knowledgeBase.createForm.${fieldKey}`)}
              isAutoFetch={false}
              initialData={options}
              allowClear={false}
              onChange={(value) => onModelChange(value, type)}
            />
          </Form.Item>
        );
      })}
    </>
  );
};

export default CreateModalBasicConfig;
