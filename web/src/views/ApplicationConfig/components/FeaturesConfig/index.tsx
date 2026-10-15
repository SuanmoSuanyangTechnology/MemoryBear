import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'antd';

import FeaturesConfigModal from './FeaturesConfigModal'
import type { FeaturesConfigModalRef, FeaturesConfigForm } from '../../types'
import type { Modality } from '@/views/ModelManagement/types'
import type { Variable } from '../VariableList/types'

/** Props for the FeaturesConfig component */
interface FeaturesConfigProps {
  /** Current feature configuration values */
  value: FeaturesConfigForm;
  /** Callback to propagate updated config back to the parent */
  refresh: (value: FeaturesConfigForm) => void;
  input_modalities?: Modality[];
  chatVariables: Variable[];
}

const FeaturesConfig: FC<FeaturesConfigProps> = ({
  value,
  refresh,
  input_modalities,
  chatVariables
}) => {
  const { t } = useTranslation();
  // Ref used to imperatively open the config modal
  const funConfigModalRef = useRef<FeaturesConfigModalRef>(null)

  /** Open the feature config modal pre-populated with the current values */
  const handleFeaturesConfig = () => {
    funConfigModalRef.current?.handleOpen(value)
  }

  return (
    <>
      <Button onClick={handleFeaturesConfig}>{t('application.features')}</Button>

      {/* Modal for editing feature settings; calls refresh on save */}
      <FeaturesConfigModal
        ref={funConfigModalRef}
        refresh={refresh}
        input_modalities={input_modalities}
        chatVariables={chatVariables}
      />
    </>
  )
}

export default FeaturesConfig
