import { useEffect, useRef } from 'react';
import type { UploadFile } from 'antd';

import UploadImages, { type UploadImagesRef } from '@/components/Upload/UploadImages';

interface RecallImageUploadProps {
  value?: string;
  onChange?: (value?: string) => void;
  disabled?: boolean;
}

const RecallImageUpload = ({ value, onChange, disabled = false }: RecallImageUploadProps) => {
  const uploadRef = useRef<UploadImagesRef>(null);

  useEffect(() => {
    if (!value) {
      uploadRef.current?.clearFiles();
    }
  }, [value]);

  const handleChange = (fileValue?: UploadFile | UploadFile[]) => {
    const file = Array.isArray(fileValue) ? fileValue[0] : fileValue;
    const dataUrl = file?.url || (file?.preview as string | undefined);
    onChange?.(dataUrl);
  };

  return (
    <UploadImages
      ref={uploadRef}
      disabled={disabled}
      fileType={['jpg', 'jpeg', 'png', 'webp', 'bmp']}
      isAutoUpload={false}
      maxCount={1}
      fileSize={10}
      multiple={false}
      onChange={handleChange}
    />
  );
};

export default RecallImageUpload;
