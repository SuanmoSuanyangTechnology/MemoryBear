import { type FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Space, App
  // Slider, Input, 
  // Form, 
  // Checkbox
} from 'antd';
import copy from 'copy-to-clipboard'

import Card from './components/Card';
// import qpsRestrictions from '@/assets/images/application/qpsRestrictions.svg'
// import dailyAdjustmentDosage from '@/assets/images/application/dailyAdjustmentDosage.svg'
// import tokenCap from '@/assets/images/application/tokenCap.svg'

// const limitList = [
//   { key: 'qpsRestrictions', value: '10', icon: qpsRestrictions, unit: ' times/second' },
//   { key: 'dailyAdjustmentDosage', value: '1000', icon: dailyAdjustmentDosage, unit: ' times/day' },
//   { key: 'tokenCap', value: '10', icon: tokenCap, unit: 'M Tokens/day' },
// ]
// const sdkList = ['pythonSDK', 'nodejsSDK', 'goSDK', 'curlExample']

const Api: FC<{apiKeyList?: string[]}> = ({apiKeyList = []}) => {
  const { t } = useTranslation();
  const [activeMethods, setActiveMethod] = useState(['GET']);
  const { message } = App.useApp()
  // const [form] = Form.useForm();
  const copyContent = window.location.origin + '/v1/chat'

  const handleCopy = (content: string) => {
    copy(content)
    message.success(t('common.copySuccess'))
  }
  return (
    <div className="rb:w-[1000px] rb:mt-[20px] rb:pb-[20px] rb:mx-auto">
      {/* <Form form={form} layout="vertical"> */}
        <Space size={20} direction="vertical" style={{width: '100%'}}>
          <Card title={t('application.endpointConfiguration')}>
            <div className="rb:p-[20px_20px_24px_20px] rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded-[8px]">
              <Space size={8}>
                {['GET', 'POST', 'PUT', 'DELETE'].map((method) => (
                  <Button key={method} type={activeMethods.includes(method) ? 'primary' : 'default'} onClick={() => setActiveMethod(prev => activeMethods.includes(method) ? prev.filter(m => m !== method) : [...prev, method])}>
                    {method}
                  </Button>
                ))}
              </Space>

              <div className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-[20px] rb:p-[20px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-[8px] rb:leading-[20px]">
                {copyContent}
                
                <Button className="rb:px-[8px]! rb:h-[28px]! rb:group" onClick={() => handleCopy(copyContent)}>
                  <div 
                    className="rb:w-[16px] rb:h-[16px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
                  ></div>
                  {t('common.copy')}
                </Button>
              </div>
            </div>
          </Card>
          <Card
            title={t('application.authenticationMethod')}
            // extra={
            //   <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAdd}>+ {t('application.addApiKey')}</Button>
            // }
          >
            <div className="rb:p-[10px_20px] rb:bg-[#F0F3F8] rb:border rb:border-[#DFE4ED] rb:rounded-[8px] rb:font-medium rb:text-center">
              {t('application.apiKeyTitle')}
              <p className="rb:mt-[6px] rb:text-[#5B6167] rb:text-[12px] rb:font-regular">{t('application.apiKeyDesc')}</p>
            </div>
            {apiKeyList.map((item, index) => (
              <div key={index} className="rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:mt-[20px] rb:p-[12px_16px] rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-[8px] rb:leading-[20px]">
                {item}

                <Space>
                  <Button className="rb:px-[8px]! rb:h-[28px]! rb:group" onClick={() => handleCopy(item)}>
                    <div 
                      className="rb:w-[16px] rb:h-[16px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/copy.svg')] rb:group-hover:bg-[url('@/assets/images/copy_active.svg')]" 
                    ></div>
                    {t('common.copy')}
                  </Button>
                  {/* <div 
                    className="rb:w-[24px] rb:h-[24px] rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]" 
                    onClick={() => handleDelete(index)}
                  ></div> */}
                </Space>
              </div>
            ))}
          </Card>
          {/* <Card title={t('application.requestResponseExample')}>
            <div className="rb:mb-[12px] rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:font-regular">
              {t('application.requestExample')}
              <Button>{t('application.downloadPostmanCollection')}</Button>
            </div>
            <div className="rb:p-[16px_20px] rb:bg-[#F0F3F8] rb:rounded-[8px] rb:text-[#5B6167] rb:leading-[18px]">
              curl -X POST  https://api.example.com/v1/agent/execute  \ -H "Authorization: Bearer YOUR_API_KEY" \ -H "Content-Type: application/json" \ -d 
            </div>

            <div className="rb:mb-[12px] rb:mt-[24px] rb:flex rb:items-center rb:justify-between rb:text-[#5B6167] rb:font-regular">
              {t('application.responseExample')}
            </div>
            <div className="rb:p-[16px_20px] rb:bg-[#F0F3F8] rb:rounded-[8px] rb:text-[#5B6167] rb:leading-[18px]">
              curl -X POST  https://api.example.com/v1/agent/execute  \ -H "Authorization: Bearer YOUR_API_KEY" \ -H "Content-Type: application/json" \ -d 
            </div>
          </Card>
          <Card title={t('application.rateLimitingStrategy')}>
            <div className="rb:grid rb:grid-cols-3 rb:gap-[18px]">
              {limitList.map(item => (
                <div key={item.key} className="rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded-[8px] rb:p-[16px_20px]">
                  <div className="rb:flex rb:justify-between">
                    <div className="rb:leading-[20px]">
                      {t(`application.${item.key}`)}
                      <div className="rb:text-[14px] rb:font-medium rb:text-[#155EEF] rb:mt-[8px]">{item.value}{item.unit}</div>
                    </div>
                    <img src={item.icon} className="rb:w-[24px] rb:h-[24px]" />
                  </div>
                  <Slider style={{ margin: '24px 0 0 0' }} value={item.value} />
                </div>
              ))}
            </div>
          </Card>
          <Card title={t('application.sdkDownload')}>
            <div className="rb:grid rb:grid-cols-4 rb:gap-[16px]">
              {sdkList.map(item => (
                <div key={item} className="rb:border rb:border-[#DFE4ED] rb:bg-[#FBFDFF] rb:rounded-[8px] rb:p-[24px_20px] rb:text-center">
                  {t(`application.${item}`)}
                </div>
              ))}
            </div>
          </Card>
          <Card title={t('application.advancedSettings')}>
            <Form.Item 
              name="WebhookReturnsTimeout"
              label={<>{t('application.WebhookReturnsTimeout')}<span className="rb:text-[#5B6167] rb:text-[12px] rb:font-regular"> ({t('application.WebhookReturnsTimeoutDesc')})</span></>}
            >
              <Input disabled />
            </Form.Item>
            <Form.Item 
              name="whitelistIP"
              label={<>{t('application.whitelistIP')}<span className="rb:text-[#5B6167] rb:text-[12px] rb:font-regular"> ({t('application.whitelistIPDesc')})</span></>}
            >
              <Input.TextArea rows={4} />
            </Form.Item>
            <Form.Item 
              name="whitelistIP"
              className="rb:mb-[0]!"
            >
              <Checkbox>{t('application.publicAPIDocumentation')}</Checkbox>
            </Form.Item>
          </Card> */}
        </Space>
      {/* </Form> */}
    </div>
  );
}
export default Api;