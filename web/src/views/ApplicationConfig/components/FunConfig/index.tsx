/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 17:20:21 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-13 17:20:21 
 */
import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';

import FunConfigModal from './FunConfigModal'
import type { FunConfigModalRef, FunConfigForm } from '../../types'

/** Props for the FunConfig component */
interface FunConfigProps {
  /** Current feature configuration values */
  value: FunConfigForm;
  /** Callback to propagate updated config back to the parent */
  refresh: (value: FunConfigForm) => void;
}

const FunConfig: FC<FunConfigProps> = ({
  value,
  refresh
}) => {
  const { t } = useTranslation();
  // Ref used to imperatively open the config modal
  const funConfigModalRef = useRef<FunConfigModalRef>(null)

  /** Open the feature config modal pre-populated with the current values */
  const handleFunConfig = () => {
    console.log('funConfig', value)
    funConfigModalRef.current?.handleOpen(value)
  }

  return (
    <>
      {/* Button that triggers the feature configuration modal */}
      <Button onClick={handleFunConfig}>{t('application.funConfig')}</Button>

      {/* Modal for editing feature settings; calls refresh on save */}
      <FunConfigModal
        ref={funConfigModalRef}
        refresh={refresh}
      />
    </>
  )
}

export default FunConfig
