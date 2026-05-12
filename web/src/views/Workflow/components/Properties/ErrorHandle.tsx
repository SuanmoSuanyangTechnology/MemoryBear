import { type FC } from "react";
import { useTranslation } from 'react-i18next'
import { Form, Select, Divider, Input, Flex } from 'antd'

import { portTextAttrs, nodeWidth, portItemArgsY } from '../../constant'

const ErrorHandle: FC<{ selectedNode?: any; graphRef?: any; }> = ({
  selectedNode,
  graphRef
}) => {
  const { t } = useTranslation()
  const form = Form.useFormInstance();
  const methodValue = Form.useWatch(['error_handle', 'method'], form) || {}

  // Handle error handling method change and update node ports accordingly
  const handleChangeErrorHandleMethod = (method: string) => {
    form.setFieldsValue({
      error_handle: {
        method,
        body: undefined,
        status_code: undefined,
        headers: undefined
      }
    })
    
    // Update node ports
    if (selectedNode && graphRef?.current) {
      const existingPorts = selectedNode.getPorts();
      const errorPort = existingPorts.find((port: any) => port.id === 'ERROR');
      
      if (method === 'branch' && !errorPort) {
        // Add error branch port
        selectedNode.addPort({
          id: 'ERROR',
          group: 'right',
          args: {
            x: nodeWidth,
            y: portItemArgsY + portItemArgsY,
          },
          attrs: { text: { text: t('workflow.config.http-request.errorBranch'), ...portTextAttrs }}
        });
      } else if (method !== 'branch' && errorPort) {
        // Remove error branch port and related edges
        const edges = graphRef.current.getEdges().filter((edge: any) => 
          edge.getSourceCellId() === selectedNode.id && edge.getSourcePortId() === 'ERROR'
        );
        edges.forEach((edge: any) => graphRef.current.removeCell(edge));
        selectedNode.removePort('ERROR');
      }
    }
  }

  return (
    <>
      <Divider className="rb:mt-0!" />
      <Flex justify="space-between" align="center">
        <div className="rb:text-[12px] rb:font-medium">{t('workflow.config.http-request.error_handle')}</div>
        <Form.Item layout="horizontal" name={['error_handle', 'method']} noStyle>
          <Select
            placeholder={t('common.pleaseSelect')}
            onChange={handleChangeErrorHandleMethod}
            options={[
              { value: 'none', label: t('workflow.config.http-request.none') },
              { value: 'default', label: t('workflow.config.http-request.default') },
              { value: 'branch', label: t('workflow.config.http-request.branch') },
            ]}
            className="rb:w-30!"
          />
        </Form.Item>
      </Flex>
      {methodValue === 'default' &&
        <Form.Item
            name={['error_handle', 'output']}
            label={<>
                <span className="rb:text-[#5B6167] rb:font-medium">output</span>
                <span className="rb:text-[#5B6167] rb:ml-1" style={{fontWeight: 400}}>string</span>
            </>}
            className="rb:my-2!"
        >
            <Input placeholder={t('common.pleaseEnter')} />
        </Form.Item>
      }
    </>
  );
};
export default ErrorHandle;