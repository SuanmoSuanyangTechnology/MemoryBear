/*
 * @Author: ZhaoYing 
 * @Date: 2026-06-05 13:33:16 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-06-05 13:33:16 
 */
import { forwardRef, useImperativeHandle, useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Tag, Switch, App, Flex, Space, Skeleton, Tooltip } from 'antd';
import clsx from 'clsx';
import { NumberOutlined, FieldStringOutlined, ClockCircleOutlined } from '@ant-design/icons';


import RbDrawer from '@/components/RbDrawer';
import MetadataModal from './MetadataModal';
import type { MetadataDrawerRef, MetadataModalRef, MetadataField } from '@/views/KnowledgeBase/types';
import { getMetadataFields, deleteMetadataField, enableBuiltInMetadata } from '@/api/knowledgeBase';

const MetadataDrawer = forwardRef<MetadataDrawerRef>((_props, ref) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [open, setOpen] = useState(false);
  const [kbId, setKbId] = useState<string>('');
  const [metadataFields, setMetadataFields] = useState<MetadataField[]>([]);
  const [builtinEnabled, setBuiltinEnabled] = useState<boolean>(true);
  const [builtinFields, setBuiltinFields] = useState<MetadataField[]>([]);
  const [loading, setLoading] = useState(false);
  const metadataModalRef = useRef<MetadataModalRef>(null);

  const fetchMetadataFields = async () => {
    if (!kbId) return;
    setLoading(true);
    getMetadataFields(kbId)
      .then(res => {
        const response = res as {
          custom: MetadataField[];
          builtin_fields: MetadataField[];
          builtin_enabled: boolean;
        };
        setMetadataFields(response?.custom || []);
        setBuiltinEnabled(response?.builtin_enabled || false);
        setBuiltinFields(response?.builtin_fields || []);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  useEffect(() => {
    if (open && kbId) {
      fetchMetadataFields();
    }
  }, [open, kbId]);

  const handleOpen = (knowledgeBaseId: string) => {
    setKbId(knowledgeBaseId);
    setOpen(true);
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleAddMetadata = () => {
    metadataModalRef.current?.handleOpen(kbId);
  };

  const handleDeleteMetadata = (vo: MetadataField) => {
    if (!kbId) return;
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: vo.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteMetadataField(kbId, vo.id)
          .then(() => {
            fetchMetadataFields();
            message.success(t('common.deleteSuccess'))
          })
      }
    })
  };
  const handleEditMetadata = (vo: MetadataField) => {
    metadataModalRef.current?.handleOpen(kbId, vo);
  };

  const handleChangeBuiltinEnabled = async () => {
    enableBuiltInMetadata(kbId, { enabled: !builtinEnabled })
      .then(() => {
        message.success(t('common.operateSuccess'));
        fetchMetadataFields();
      })
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  return (
    <>
      <RbDrawer
        title={t('knowledgeBase.metadata.label')}
        open={open}
        onClose={handleClose}
        width={520}
      >
        <div className="rb:pb-4">
          <div className="rb:mb-4 rb:text-[12px] rb:text-[#5B6167]">
            {t('knowledgeBase.metadata.description')}
          </div>
          
          <Button
            type="primary"
            className="rb:mb-4"
            disabled={loading}
            onClick={handleAddMetadata}
          >
            + {t('knowledgeBase.metadata.add')}
          </Button>

          {loading
            ? <Skeleton active={true} />
            : <>
              <Flex vertical gap={12}>
                {metadataFields.map(item => (
                  <Flex
                    key={item.id}
                    align="center"
                    justify="space-between"
                    className="rb:w-full! rb-border rb:rounded-lg rb:p-2! rb:relative rb:group"
                  >
                    <div className="rb:flex rb:items-center rb:gap-2">
                      <span className="rb:text-[#5B6167] rb:text-[12px]">
                        {item.type === 'string'
                          ? <FieldStringOutlined />
                          : item.type === 'number'
                          ? <NumberOutlined />
                          : <ClockCircleOutlined />
                        }
                      </span>
                      <span className="rb:font-medium">{item.name}</span>
                      <Tag color="blue">{item.type}</Tag>
                    </div>
                    <span className="rb:text-[12px] rb:text-[#5B6167]">
                      {item.count || 0} {t('knowledgeBase.metadata.valueCount')}
                    </span>
                    <Space size={8} className="rb:absolute rb:right-0 rb:hidden! rb:group-hover:inline-flex! rb:bg-white rb:p-2! rb:rounded-tr-lg rb:rounded-br-lg">
                      <div
                        className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
                        onClick={() => handleEditMetadata(item)}
                      ></div>
                      <div
                        className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                        onClick={() => handleDeleteMetadata(item)}
                      ></div>
                    </Space>
                  </Flex>
                ))}
              </Flex>

              <Flex gap={8} align="center" className="rb:mt-6! rb:mb-3!">
                <Switch
                  checked={builtinEnabled}
                  onChange={handleChangeBuiltinEnabled}
                />
                {t('knowledgeBase.metadata.builtin')}
                <Tooltip title={t('knowledgeBase.metadata.builtinTip')}>
                  <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')] rb:shrink-0"></div>
                </Tooltip>
              </Flex>

              <Flex vertical gap={12}>
                {builtinFields.map(item => (
                  <Flex
                    key={item.id}
                    align="center"
                    justify="space-between"
                    className={clsx("rb:w-full! rb-border rb:rounded-lg rb:p-2! rb:relative rb:group", {
                      'rb:opacity-65': !builtinEnabled,
                    })}
                  >
                    <div className="rb:flex rb:items-center rb:gap-2">
                      <span className="rb:text-[#5B6167] rb:text-[12px]">
                        {item.type === 'string'
                          ? <FieldStringOutlined />
                          : item.type === 'number'
                          ? <NumberOutlined />
                          : <ClockCircleOutlined />
                        }
                      </span>
                      <span className="rb:font-medium">{item.name}</span>
                      <Tag color="blue">{item.type}</Tag>
                    </div>
                    {builtinEnabled
                      ? <span className="rb:text-[12px] rb:text-[#5B6167]">
                        {item.count || 0} {t('knowledgeBase.metadata.valueCount')}
                      </span>
                      : t('knowledgeBase.metadata.builtinDisabled')
                    }
                  </Flex>
                ))}
              </Flex>
            </>
          }
        </div>
      </RbDrawer>

      <MetadataModal
        ref={metadataModalRef}
        refresh={fetchMetadataFields}
      />
    </>
  );
});

export default MetadataDrawer;
