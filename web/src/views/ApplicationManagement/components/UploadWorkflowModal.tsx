import { forwardRef, useImperativeHandle, useState, useMemo } from 'react';
import { Form, Select, Steps, Flex, Alert, Row, Col, Statistic, Input, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import type { UploadWorkflowModalData, UploadWorkflowModalRef } from '../types'
import RbModal from '@/components/RbModal'
import UploadFiles from '@/components/Upload/UploadFiles'
import { fileUploadUrl } from '@/api/fileStorage'
import RbCard from '@/components/RbCard/Card'

interface UploadWorkflowModalProps {
  refresh: () => void;
}
const steps = [
  'upload',
  'complex',
  'node',
  'configCheck',
  'sureInfo',
  'completed'
]
const UploadWorkflowModal = forwardRef<UploadWorkflowModalRef, UploadWorkflowModalProps>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [form] = Form.useForm<UploadWorkflowModalData>();
  const [loading, setLoading] = useState(false)
  const [current, setCurrent] = useState<number>(5);

  // 封装取消方法，添加关闭弹窗逻辑
  const handleClose = () => {
    setVisible(false);
    form.resetFields();
    setLoading(false)
  };

  const handleOpen = () => {
    form.resetFields();
    setVisible(true);
  };
  // 封装保存方法，添加提交逻辑
  const handleSave = () => {
    switch(current) {
      case 0:
        setCurrent(1)
        break;
      case 1:
        setCurrent(2)
        break;
      case 2:
        setCurrent(3)
        break;
      case 3:
        setCurrent(4)
        break;
      case 4:
        setCurrent(5)
        break;
      case 5:
        break;
      default:
        setCurrent(prev => prev + 1)
        break;
    }
    // form
    //   .validateFields()
    //   .then(() => {
    //   })
    //   .catch((err) => {
    //     console.log('err', err)
    //   });
  }

  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const handleLastStep = () => {
    setCurrent(prev => prev - 1)
  }
  const handleJump = (type: string) => {
    switch(type) {
      case 'detail':
        break;
      default: 
        break;
    }
  }

  const getFooter = useMemo(() => {
    switch(current) {
      case 0:
        return [
          <Button key="back" onClick={handleClose}>
            {t('common.cancel')}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={loading}
            onClick={handleSave}
          >
            {t('application.nextStep')}
          </Button>
        ]
      case 5:
        return [
          <Button key="back" onClick={() => handleJump('list')}>
            {t('application.gotoList')}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={loading}
            onClick={() => handleJump('detail')}
          >
            {t('application.gotoDetail')}
          </Button>
        ]
      default:
        return [
          <Button onClick={handleClose}>
            {t('common.cancel')}
          </Button>,
          <Button key="back" onClick={handleLastStep}>
            {t('application.lastStep')}
          </Button>,
          <Button
            key="submit"
            type="primary"
            loading={loading}
            onClick={handleSave}
          >
            {t('application.nextStep')}
          </Button>
        ]
    }
  }, [current])

  return (
    <RbModal
      title={t('application.importWorkflow')}
      open={visible}
      onCancel={handleClose}
      okText={t('application.nextStep')}
      onOk={handleSave}
      footer={getFooter}
      width={1000}
    >
      <div className='rb:p-3 rb:bg-[#FBFDFF] rb:rounded-lg rb:border rb:border-[#DFE4ED] rb:mb-3'>
        <Steps
          labelPlacement="vertical"
          size="small"
          current={current}
          items={steps.map(key => ({ title: t(`application.${key}`) }))}
        />
      </div> 
      {current === 0 &&
        <Form
          form={form}
          layout="vertical"
        >
          <Form.Item name="provider" label={t('application.workflowProvider')}>
            <Select
              placeholder={t('common.pleaseSelect')}
              options={[
                { label: 'Dify', value: 'dify' },
              ]}
            />
          </Form.Item>
          <Form.Item name="file" valuePropName="fileList" noStyle>
            <UploadFiles
              action={fileUploadUrl}
              isCanDrag={true}
              fileSize={100}
              multiple={true}
              maxCount={1}
              fileType={['yml', 'yaml', 'zip', 'json']}
              onChange={(fileList) => {
                console.log('文件列表变化:', fileList);
              }}
            />
          </Form.Item>
        </Form>
      }

      {current === 1 &&
        <Flex vertical gap={12} className="rb:w-[70%]! rb:mx-auto!">
          {['fileType', 'parse', 'nodes', 'variable'].map(key => (
            <Alert key={key} message={key} type="success" showIcon />
          ))}

          <Row gutter={12}>
            {['complex', 'nodes', 'task'].map(key => (
              <Col key={key} span={8}>
                <Statistic title={key} value={0} className="rb:text-center rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:py-3!" />
              </Col>
            ))}
          </Row>
        </Flex>
      }

      {/* 节点映射 */}
      {current === 2 &&
        <Flex vertical gap={12} className="rb:w-[70%]! rb:mx-auto!">
          <RbCard>
            <Flex justify="space-around">
              <div> Left Node</div>
              →
              <div>
                <Select
                  placeholder={t('common.pleaseSelect')}
                  className="rb:w-50"
                />
              </div>
            </Flex>
          </RbCard>
        </Flex>
      }
      {current === 3 &&
        <Flex vertical gap={12} className="rb:w-[70%]! rb:mx-auto!">

        </Flex>
      }
      {current === 4 &&
        <Form form={form} layout="horizontal" className="rb:w-[70%]! rb:mx-auto!">
          <div className="rb:text-[#5B6167] rb:font-medium">{t('application.baseInfo')}</div>
          <Form.Item name="name" label={t('application.workflowName')} rules={[{ required: true }]}>
            <Input placeholder={t('common.pleaseEnter')} />
          </Form.Item>
          <Form.Item name="source" label={t('application.source')}>
            source
          </Form.Item>
          <Form.Item name="fileName" label={t('application.fileName')}>
            fileName
          </Form.Item>
          <Form.Item name="fileSize" label={t('application.fileSize')}>
            fileSize
          </Form.Item>
          <Form.Item name="desciption" label={t('application.desciption')}>
            <Input.TextArea placeholder={t('common.pleaseEnter')} />
          </Form.Item>

          <div className="rb:text-[#5B6167] rb:font-medium">{t('application.importStatistic')}</div>
          <Row gutter={12}>
            {['complex', 'nodes', 'task'].map(key => (
              <Col key={key} span={8}>
                <Statistic title={key} value={0} className="rb:text-center rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:py-3!" />
              </Col>
            ))}
          </Row>
        </Form>
      }
      {current === 5 &&
        <Flex justify="center" vertical gap={12} className="rb:w-[70%]! rb:mx-auto! rb:text-center">
          <div>导入成功</div>
          <div>您的工作流已成功导入，可以在应用管理中查看和管理</div>
        </Flex>
      }
    </RbModal>
  );
});

export default UploadWorkflowModal;