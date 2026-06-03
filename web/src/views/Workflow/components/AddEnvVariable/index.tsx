/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-03 19:53:01 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-03 19:53:01 
 */
import { useState, useImperativeHandle, forwardRef, useRef } from 'react';
import { Button, List, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import type { EnvVariable, AddEnvVariableRef } from '../../types';
import type { EnvVariableModalRef } from './types'
import RbDrawer from '@/components/RbDrawer';
import Empty from '@/components/Empty';
import EnvVariableModal from './EnvVariableModal';

interface AddEnvVariableProps {
  variables?: EnvVariable[];
  onChange?: (variables: EnvVariable[]) => void;
  disabled?: boolean;
  maxVariables?: number;
}
const AddEnvVariable = forwardRef<AddEnvVariableRef, AddEnvVariableProps>(({
  variables = [],
  onChange,
}, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const envVariableRef = useRef<EnvVariableModalRef>(null);

  const handleAddVariable = () => {
    envVariableRef.current?.handleOpen()
  };

  const handleEdit = (index: number) => {
    envVariableRef.current?.handleOpen(variables[index], index)
  }
  const handleDelete = (index: number) => {
    const list = [...variables]
    list.splice(index, 1)
    onChange && onChange(list)
  }

  const handleOpen = () => {
    setOpen(true)
  }
  const handleSave = (value: EnvVariable, index?: number) => {
    const list = [...variables]
    if (typeof index === 'number' && index > -1) {
      list[index] = value
    } else {
      list.push(value)
    }
    onChange && onChange(list)
  }
  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  return (
    <RbDrawer
      title={t('workflow.addEnvVariable')}
      open={open}
      onClose={() => setOpen(false)}
      width={480}
    >
      <div>
        <Button
          type="primary"
          className="rb:mb-3"
          onClick={handleAddVariable}
        >
          + {t('workflow.addEnvVariable')}
        </Button>

        {variables.length === 0
          ? <Empty size={88} />
          :
          <List
            grid={{ gutter: 12, column: 1 }}
            dataSource={variables}
            renderItem={(item, index) => (
              <List.Item>
                <div key={index} className="rb:relative rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:cursor-pointer rb-border rb:rounded-lg">
                  <Flex align="center" justify="space-between" className="rb:leading-4 rb:max-w-[calc(100%-60px)]">
                    <div className="rb:flex-1 rb:font-medium rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular">
                      {item.required && <span className="rb:text-[#FF4D4F]">*</span>}
                      ({t('workflow.env-variable.secret')})
                    </div>
                  </Flex>
                  <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:wrap-break-word rb:line-clamp-1 rb:max-w-[calc(100%-60px)]">
                    ********************
                  </div>
                  <Flex gap={12} className="rb:absolute rb:right-4 rb:top-[50%] rb:transform-[translateY(-50%)] rb:bg-white">
                    <div
                      className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                      onClick={() => handleEdit(index)}
                    ></div>
                    <div
                      className="rb:size-5 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                      onClick={() => handleDelete(index)}
                    ></div>
                  </Flex>
                </div>
              </List.Item>
            )}
          />
        }
      </div>

      <EnvVariableModal
        ref={envVariableRef}
        refresh={handleSave}
        variables={variables}
      />
    </RbDrawer>
  );
});

export default AddEnvVariable;