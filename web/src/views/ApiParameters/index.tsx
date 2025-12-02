import { useTranslation } from 'react-i18next';
import { type FC, useEffect, useState } from 'react';
import { Row, Col, Skeleton } from 'antd'
import CodeBlock from '@/components/Markdown/CodeBlock';
import { getMemoryApi } from '@/api/memory';
import RbCard from '@/components/RbCard/Card';
import type { 
  Data, 
  Section 
} from './types';
import Empty from '@/components/Empty'


const ApiParameters: FC = () => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState<boolean>(false)
  // const [data, setData] = useState<Data | null>(null)
  const [apiData, setApiData] = useState<Section[]>([])

  useEffect(() => {
    getApiData()
  }, [])
  const getApiData = () => {
    setLoading(true)
    getMemoryApi().then((res) => {
      const resp = res as Data || {}
      // setData(resp)
      setApiData(resp.sections || [])
    })
    .finally(() => setLoading(false))
  }

  return (
    <div className="rb:pb-[24px]">
      <h1 className="rb:text-2xl rb:font-semibold rb:mb-[8px]">{t('api.pageTitle')}</h1>
      <p className="rb:text-[#5B6167] rb:text-[14px] rb:mb-[24px] rb:leading-[20px]">{t('api.pageSubTitle')}</p>

      {loading 
        ? <Skeleton /> 
        : apiData.length === 0
        ? <Empty />
        : <Row gutter={[24, 24]}>
          {apiData.map((api, index) => (
            <Col span={24} key={index}>
              <RbCard
                title={`${index + 1}. ${api.name}`}
              >
                <>
                  <div className="rb:mb-[24px] rb:bg-[#F0F3F8] rb:rounded-[8px] rb:shadow-[inset_4px_0px_0px_0px_#155EEF] rb:p-[16px_24px] rb:text-sm">
                    <span className="rb:bg-[#155EEF] rb:p-[2px_8px] rb:rounded-[6px] rb:text-[#fff] rb:mr-[16px]">{api.method}</span>
                    {api.path}
                  </div>
                  {api.desc &&<>
                    <div className="rb:text-base rb:font-medium rb:mb-[8px]">{t('api.desc')}</div>
                    <div className="rb:mb-[24px] rb:text-sm rb:text-[#5B6167]">{api.desc}</div>
                  </>}

                  {typeof api.input === 'string' && api.input !== '无' && <>
                    <div className="rb:text-base rb:font-medium rb:mb-[12px] rb:mt-[24px]">{t('api.input')}</div>
                    <CodeBlock value={api.input} />
                  </>}
                  {typeof api.output === 'string' && api.output !== '无' && <>
                    <div className="rb:text-base rb:font-medium rb:mb-[12px] rb:mt-[24px]">{t('api.output')}</div>
                    <CodeBlock value={api.output} />
                  </>}
                </>
              </RbCard>
            </Col>
          ))}
        </Row>
      }
    </div>
  );
};
export default ApiParameters;