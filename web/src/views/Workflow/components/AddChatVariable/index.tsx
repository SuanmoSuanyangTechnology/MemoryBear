import { useState, useImperativeHandle, forwardRef, useRef } from 'react';
import { Button, Space, List } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ChatVariable, AddChatVariableRef } from '../../types';
import type { ChatVariableModalRef } from './types'
import RbDrawer from '@/components/RbDrawer';
import Empty from '@/components/Empty';
import ChatVariableModal from './ChatVariableModal';

interface AddChatVariableProps {
  variables?: ChatVariable[];
  onChange?: (variables: ChatVariable[]) => void;
  disabled?: boolean;
  maxVariables?: number;
}
const AddChatVariable = forwardRef<AddChatVariableRef, AddChatVariableProps>(({
  variables = [],
  onChange,
}, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const chatVariableRef = useRef<ChatVariableModalRef>(null);

  const handleAddVariable = () => {
    chatVariableRef.current?.handleOpen()
  };

  const handleEdit = (index: number) => {
    chatVariableRef.current?.handleOpen(variables[index], index)
  }
  const handleDelete = (index: number) => {
    const list = [...variables]
    list.splice(index, 1)
    onChange && onChange(list)
  }

  const handleOpen = () => {
    setOpen(true)
  }
  const handleSave = (value: ChatVariable, index?: number) => {
    const list = [...variables]
    if (typeof index === 'number' && index > -1) {
      list[index] = value
    } else {
      list.push(value)
    }
    onChange && onChange(list)
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  return (
    <RbDrawer
      title={t('workflow.addvariable')}
      open={open}
      onClose={() => setOpen(false)}
    >
      <div>
        <Button
          type="primary"
          className="rb:mb-3"
          onClick={handleAddVariable}
        >
          + {t('workflow.addChatVariable')}
        </Button>

        {variables.length === 0
          ? <Empty size={88} />
          :
          <List
            grid={{ gutter: 12, column: 1 }}
            dataSource={variables}
            renderItem={(item, index) => (
              <List.Item>
                <div key={index} className="rb:relative rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:cursor-pointer rb:border rb:border-[#DFE4ED] rb:rounded-lg">
                  <div className="rb:flex rb:items-center rb:justify-between">
                    <div className="rb:leading-4">
                      <span className="rb:font-medium">{item.name}</span>
                      <span className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular"> ({t(`workflow.config.parameter-extractor.${item.type}`)})</span>
                    </div>
                  </div>
                  <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5 rb:wrap-break-word rb:line-clamp-1">{item.description}</div>
                  <Space size={12} className="rb:flex rb:absolute rb:right-4 rb:top-[50%] rb:transform-[translateY(-50%)] rb:bg-white">
                    <div
                      className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                      onClick={() => handleEdit(index)}
                    ></div>
                    <div
                      className="rb:size-5 rb:cursor-pointer rb:bg-cover  rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                      onClick={() => handleDelete(index)}
                    ></div>
                  </Space>
                </div>
              </List.Item>
            )}
          />
        }
      </div>

      <ChatVariableModal
        ref={chatVariableRef}
        refresh={handleSave}
      />
    </RbDrawer>
  );
});

export default AddChatVariable;