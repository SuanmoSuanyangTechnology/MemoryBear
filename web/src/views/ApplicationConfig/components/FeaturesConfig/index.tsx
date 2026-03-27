/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 17:20:21 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 19:07:35
 */
import { type FC, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Popover } from 'antd';

import FeaturesConfigModal from './FeaturesConfigModal'
import type { FeaturesConfigModalRef, FeaturesConfigForm } from '../../types'
import type { Application } from '@/views/ApplicationManagement/types';
import type { Capability } from '@/views/ModelManagement/types'
import type { Variable } from '../VariableList/types'

/** Props for the FeaturesConfig component */
interface FeaturesConfigProps {
  /** Current feature configuration values */
  value: FeaturesConfigForm;
  /** Callback to propagate updated config back to the parent */
  refresh: (value: FeaturesConfigForm) => void;
  source?: Application['type'];
  capability?: Capability[];
  chatVariables: Variable[];
}

const FeaturesConfig: FC<FeaturesConfigProps> = ({
  value,
  refresh,
  source,
  capability,
  chatVariables
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
      {source === 'workflow'
        ?
        <Popover content={t('application.features')} classNames={{ body: 'rb:py-0.5! rb:px-1! rb:rounded-[6px]! rb:text-[12px]!' }}>
          <div
            className="rb:cursor-pointer rb:size-7.5 rb:border rb:border-[#EBEBEB] rb:hover:bg-[#F6F6F6] rb:rounded-[10px] rb:bg-[url('src/assets/images/workflow/features.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
            onClick={handleFeaturesConfig}
          ></div>
        </Popover>
        : <Button onClick={handleFeaturesConfig}>{t('application.features')}</Button>
      }

      {/* Modal for editing feature settings; calls refresh on save */}
      <FeaturesConfigModal
        ref={funConfigModalRef}
        refresh={refresh}
        source={source}
        capability={capability}
        chatVariables={chatVariables}
      />
    </>
  )
}

export default FeaturesConfig
