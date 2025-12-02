
import { forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { Form, Input,  Select, Button, InputNumber } from 'antd';
import { useTranslation } from 'react-i18next';
import type { RecallTestDrawerRef, RecallTestData, RecallTestParams } from '../types';
// import refreshIcon from '@/assets/images/knowledgeBase/refresh-blue.png';
import RecallTestResult from './RecallTestResult';
import { reChunks, getRetrievalModeType } from '../service';
import { hybrid } from 'react-syntax-highlighter/dist/esm/styles/hljs';

const { TextArea } = Input;

interface RetrievalModeOption {
    label: string;
    value: boolean;
}

const RecallTest = forwardRef<RecallTestDrawerRef>(({},ref) => {
    const [form] = Form.useForm();
    const { t } = useTranslation();
    const [data, setData] = useState<RecallTestData[]>([]);
    const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>('');
    const [loading, setLoading] = useState(false);
    const [retrieveType, setRetrieveType] = useState<string>('hybrid');
    const [retrievalModeOptions, setRetrievalModeOptions] = useState<RetrievalModeOption[]>([
        { label: t('knowledgeBase.hybrid'), value: true },
        { label: t('knowledgeBase.vector'), value: false },
    ]);

    // 获取检索模式选项
    useEffect(() => {
        fetchRetrievalModeOptions();
    }, []);

    const fetchRetrievalModeOptions = async () => {
        try {
            const response = await getRetrievalModeType();
            if (response && Array.isArray(response)) {
                // 将 API 返回的数据转换为选项格式
                const options = response.map((item: any) => {
                    // 支持多种数据格式
                    let label = t(`knowledgeBase.${item}`) + ' ' + t(`knowledgeBase.retrieve`);
                    let value = item;
                    
                    return { label, value };
                });
                
                if (options.length > 0) {
                    setRetrievalModeOptions(options);
                }
            }
        } catch (error) {
            console.error('获取检索模式选项失败:', error);
            // 保持默认选项
        }
    };

    const handleOpen = (kbId?: string) => {
        console.log('RecallTest - handleOpen called with kbId:', kbId);
        setKnowledgeBaseId(kbId || '');
        form.resetFields();
        setData([]);
        setRetrieveType('hybrid'); // 重置为默认值
        // 确保表单字段也设置为默认值
        form.setFieldsValue({ retrieve_type: 'hybrid' });
    }
    const fetchData = (params: RecallTestParams) => {
        if (loading) return;
        setLoading(true);
        console.log('params', params);
        reChunks(params)
          .then((res) => {
            const response = res as  RecallTestData[] ;
            setData(response || [])
          })
          .finally(() => {
            setLoading(false);
          });
    }
    const handleStartTest = () => {
        form.validateFields().then((values) => {
            const params: RecallTestParams = {
                query: values.query || '',
                kb_ids: knowledgeBaseId ? [knowledgeBaseId] : [],
                similarity_threshold: values.similarity_threshold || 0.2,
                vector_similarity_weight: values.vector_similarity_weight || 0.3,
                top_k: values.top_k || 1024,
                // hybrid: values.retrieve_type !== hybrid ? true : false,
                retrieve_type: retrieveType,
            };
            console.log('RecallTest - params:', params);
            fetchData(params);
        }).catch((error) => {
            console.error('表单验证失败:', error);
        });
    }
    // 暴露给父组件的方法
    useImperativeHandle(ref, () => ({
        handleOpen,
    }));
  return (
    <div className='rb:w-full rb:h-full rb:flex rb:flex-col rb:overflow-hidden'>
      <div className='rb:flex-shrink-0'>
        <div className='rb:flexx rb:mb-2 rb:items-center rb:justify-between'>
          <span className='rb:font-medium'>{ t('knowledgeBase.testQuestion')}</span>
          {/* <div className='rb:flex rb:items-center rb:justify-end'>
              <img src={refreshIcon} alt="refresh" className='rb:w-4 rb:h-4 rb:mr-2' />
              <span className='rb:text-[#155eef]'>{ t('knowledgeBase.loadSampleQuestions')}</span>
          </div> */}
        </div>
        <Form form={form} >
          <Form.Item name="query">
              <TextArea rows={4} placeholder={t('knowledgeBase.testQuestionPlaceholder')}/>
          </Form.Item>
          <div className='rb:grid rb:grid-cols-2 rb:gap-x-4'>
              <Form.Item 
                  name="retrieve_type" 
                  label={t('knowledgeBase.retrieveMode')}
                  initialValue="hybrid"
              > 
                  <Select
                      options={retrievalModeOptions}
                      placeholder={t('knowledgeBase.retrieveMode')}
                      onChange={(value) => setRetrieveType(value)}
                  />
              </Form.Item>
              
              <Form.Item name="top_k" label={t('knowledgeBase.recallQuantity')}>
                  <InputNumber 
                      placeholder='1 ~ 1024'
                      min={1}
                      max={1024}
                      style={{ width: '100%' }}
                  />
              </Form.Item>

              {/* 当 retrieve_type = semantic 或 hybrid 时显示 */}
              {(retrieveType === 'semantic' || retrieveType === 'hybrid') && (
                  <Form.Item name="similarity_threshold" label={t('knowledgeBase.similarityThreshold')}>
                      <Select
                          options={[
                              { label: '0.1', value: 0.1 },
                              { label: '0.2', value: 0.2 },
                              { label: '0.3', value: 0.3 },
                              { label: '0.4', value: 0.4 },
                              { label: '0.5', value: 0.5 },
                              { label: '0.6', value: 0.6 },
                              { label: '0.7', value: 0.7 },
                              { label: '0.8', value: 0.8 },
                              { label: '0.9', value: 0.9 },
                              { label: '1.0', value: 1.0 },
                          ]}
                          placeholder={t('knowledgeBase.similarityThreshold')}
                      />
                  </Form.Item>
              )}

              {/* 当 retrieve_type = participle 或 hybrid 时显示 */}
              {(retrieveType === 'participle' || retrieveType === 'hybrid') && (
                  <Form.Item name="vector_similarity_weight" label={t('knowledgeBase.semanticSimilarity')}>
                      <Select
                          options={[
                              { label: '0.1', value: 0.1 },
                              { label: '0.2', value: 0.2 },
                              { label: '0.3', value: 0.3 },
                              { label: '0.4', value: 0.4 },
                              { label: '0.5', value: 0.5 },
                              { label: '0.6', value: 0.6 },
                              { label: '0.7', value: 0.7 },
                              { label: '0.8', value: 0.8 },
                              { label: '0.9', value: 0.9 },
                              { label: '1.0', value: 1.0 },
                          ]}
                          placeholder={t('knowledgeBase.semanticSimilarity')}
                      />
                  </Form.Item>
              )}  
                
              {/* <Form.Item name="hybrid" valuePropName="checked" initialValue={true} label={t('knowledgeBase.hybrid') || 'Hybrid'}>
                  <Switch checkedChildren={t('common.yes') || 'Yes'} unCheckedChildren={t('common.no') || 'No'} />
              </Form.Item>  */}
              <Form.Item>
                  <Button type="primary" onClick={handleStartTest} loading={loading}>{ t('knowledgeBase.startTesting')}</Button>
              </Form.Item> 
          </div>
          {/* <div className='rb:flex rb:items-center rb:justify-end'>
              
          </div> */}
        </Form>
      </div>
      <div className='rb:flex-1 rb:overflow-y-auto rb:min-h-0'>
          <RecallTestResult data={data} showEmpty={true} />
      </div>
      
    </div>
  );
});

export default RecallTest;