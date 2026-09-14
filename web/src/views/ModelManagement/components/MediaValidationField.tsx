import { Form, Input } from 'antd';
import { useTranslation } from 'react-i18next';

interface MediaValidationFieldProps {
  mediaType?: 'audio' | 'video';
  name?: string | string[];
}

const MediaValidationField = ({ mediaType, name = 'test_media_url' }: MediaValidationFieldProps) => {
  const { t } = useTranslation();
  if (!mediaType) return null;
  return (
    <Form.Item
      name={name}
      preserve={false}
      label={t(mediaType === 'audio' ? 'modelNew.testAudioUrl' : 'modelNew.testVideoUrl')}
      extra={t(mediaType === 'audio' ? 'modelNew.asrValidationHint' : 'modelNew.videoValidationHint')}
      rules={[
        { required: true, message: t('modelNew.mediaUrlRequired') },
        { type: 'url', message: t('modelNew.mediaUrlInvalid') },
        { pattern: /^https?:\/\//i, message: t('modelNew.mediaUrlInvalid') },
      ]}
    >
      <Input />
    </Form.Item>
  );
};

export default MediaValidationField;
