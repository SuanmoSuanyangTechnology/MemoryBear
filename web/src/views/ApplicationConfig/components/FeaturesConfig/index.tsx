/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 17:20:21 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-18 15:38:59
 */
import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';

import FeaturesConfigModal from './FeaturesConfigModal'
import type { FeaturesConfigModalRef, FeaturesConfigForm } from '../../types'
import type { Application } from '@/views/ApplicationManagement/types';

/** Props for the FeaturesConfig component */
interface FeaturesConfigProps {
  /** Current feature configuration values */
  value: FeaturesConfigForm;
  /** Callback to propagate updated config back to the parent */
  refresh: (value: FeaturesConfigForm) => void;
  source?: Application['type'];
}

const FeaturesConfig: FC<FeaturesConfigProps> = ({
  value,
  refresh,
  source
}) => {
  const { t } = useTranslation();
  // Ref used to imperatively open the config modal
  const funConfigModalRef = useRef<FeaturesConfigModalRef>(null)

  /** Open the feature config modal pre-populated with the current values */
  const handleFeaturesConfig = () => {
    console.log('handleFeaturesConfig', value)
    funConfigModalRef.current?.handleOpen(value)
  }

  return (
    <>
      {/* Button that triggers the feature configuration modal */}
      <Button onClick={handleFeaturesConfig}>{t('application.features')}</Button>

      {/* Modal for editing feature settings; calls refresh on save */}
      <FeaturesConfigModal
        ref={funConfigModalRef}
        refresh={refresh}
        source={source}
      />
    </>
  )
}

export default FeaturesConfig
