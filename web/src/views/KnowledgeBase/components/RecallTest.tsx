import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  useMemo,
} from 'react';
import { Button, Flex, Form, Input, InputNumber, Select, Switch } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type {
  RecallTestData,
  RecallTestDrawerRef,
  RecallTestParams,
  RetrievalPolicy,
} from '@/views/KnowledgeBase/types';
import { getRetrievalModeType, reChunks, retrievalPolicyApi } from '@/api/knowledgeBase';
import ModelSelect from '@/components/ModelSelect';
import WeightBalanceSlider from '@/components/Knowledge/WeightBalanceSlider';
import RecallImageUpload from './RecallImageUpload';
import RecallTestResult from './RecallTestResult';

const { TextArea } = Input;

interface RetrievalModeOption {
  label: string;
  value: string;
}

const RecallTest = forwardRef<RecallTestDrawerRef>((props, ref) => {
  void props;
  const [form] = Form.useForm();
  const { t } = useTranslation();
  const [data, setData] = useState<RecallTestData[]>([]);
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const layoutContainerRef = useRef<HTMLDivElement>(null);
  const [isWideLayout, setIsWideLayout] = useState(false);
  const [retrievalModeOptions, setRetrievalModeOptions] = useState<RetrievalModeOption[]>([
    { label: t('knowledgeBase.hybrid'), value: 'hybrid' },
    { label: t('knowledgeBase.vector'), value: 'semantic' },
  ]);

  const formValues = Form.useWatch([], form) || {};

  useLayoutEffect(() => {
    const container = layoutContainerRef.current;
    if (!container) return;

    const updateLayout = (width: number) => {
      const nextIsWideLayout = width > 900;
      setIsWideLayout((current) =>
        current === nextIsWideLayout ? current : nextIsWideLayout,
      );
    };

    updateLayout(container.getBoundingClientRect().width);
    const resizeObserver = new ResizeObserver(([entry]) => {
      updateLayout(entry.contentRect.width);
    });
    resizeObserver.observe(container);

    return () => resizeObserver.disconnect();
  }, []);

  console.log('RecallTest - knowledgeBaseId:', knowledgeBaseId);
  // Get retrieval mode options
  useEffect(() => {
    const fetchRetrievalModeOptions = async () => {
      try {
        const response = await getRetrievalModeType();
        if (response && Array.isArray(response)) {
          // Convert API response to option format
          const options = response.map((item: any) => {
            // Support multiple data formats
            const label = t(`knowledgeBase.${item}`) + ' ' + t('knowledgeBase.retrieve');
            const value = item;

            return { label, value };
          });

          if (options.length > 0) {
            setRetrievalModeOptions(options);
          }
        }
      } catch (error) {
        console.error('Failed to fetch retrieval mode options:', error);
        // Keep default options
      }
    };

    void fetchRetrievalModeOptions();
  }, [t]);

  const [retrievalPolicy, setRetrievalPolicy] = useState<RetrievalPolicy>({});
  const retrievalPolicyRequestIdRef = useRef(0);

  const supportsImage = useMemo(() => {
    const { retrieve_type, rerank_mode, enable_graph_retrieval } = formValues || {};
    return retrievalPolicy[retrieve_type]?.includes('image')
    && !(retrieve_type === 'hybrid' && rerank_mode === 'weighted_score')
    && !(retrieve_type === 'hybrid' && enable_graph_retrieval);
  }, [retrievalPolicy, formValues])

  useEffect(() => {
    if (!supportsImage) {
      form.setFieldValue('image', undefined);
    }
  }, [form, supportsImage]);

  const getRetrievalPolicy = (kbId: string) => {
    const requestId = retrievalPolicyRequestIdRef.current + 1;
    retrievalPolicyRequestIdRef.current = requestId;

    retrievalPolicyApi({ kb_ids: kbId ? [kbId] : [] })
      .then((policy) => {
        if (requestId === retrievalPolicyRequestIdRef.current) {
          setRetrievalPolicy(policy);
        }
      })
      .catch(() => {
        if (requestId === retrievalPolicyRequestIdRef.current) {
          setRetrievalPolicy({});
        }
      });
  };

  const handleOpen = (kbId?: string) => {
    const nextKnowledgeBaseId = kbId || '';
    setKnowledgeBaseId(nextKnowledgeBaseId);
    setRetrievalPolicy({});
    form.resetFields();
    setData([]);
    // Ensure form field is also set to default value
    form.setFieldsValue({ retrieve_type: 'hybrid', rerank_mode: 'reranking_model' });
    getRetrievalPolicy(nextKnowledgeBaseId);
  };

  const fetchData = (params: RecallTestParams) => {
    if (loading) return;
    setLoading(true);
    reChunks(params)
      .then((res) => {
        const response = res as RecallTestData[];
        setData(response || []);
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const handleStartTest = () => {
    form
      .validateFields()
      .then(({
        image,
        query,
        rerank_mode,
        retrieve_type,
        similarity_threshold,
        vector_similarity_weight,
        top_k,
        enable_graph_retrieval,
        reranker_id,
        rerank_weights,
      }) => {
        image = supportsImage ? image : undefined;
        const params: RecallTestParams = {
          query: image
            ? { modality: 'image', content: image }
            : { modality: 'text', content: query || '' },
          kb_ids: knowledgeBaseId ? [knowledgeBaseId] : [],
          similarity_threshold:
            retrieve_type === 'participle' ? undefined : similarity_threshold || 0.2,
          vector_similarity_weight: vector_similarity_weight || 0.3,
          top_k: top_k || 100,
          // hybrid: values.retrieve_type !== hybrid ? true : false,
          retrieve_type,
          enable_graph_retrieval:
            retrieve_type === 'hybrid' ? enable_graph_retrieval : undefined,
          ...(retrieve_type === 'hybrid' && rerank_mode
            ? {
                rerank_mode,
                ...(rerank_mode === 'reranking_model'
                  ? { reranker_id }
                  : { rerank_weights  }
                ),
              }
            : {}),
        };
        console.log('RecallTest - params:', params);
        fetchData(params);
      })
      .catch((error) => {
        console.error('Form validation failed:', error);
      });
  };

  // Expose methods to parent component
  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const handleChangeRerankMode = (value: string | null | undefined) => {
    if (value === 'reranking_model') {
      form.setFieldsValue({ rerank_weights: undefined, enable_graph_retrieval: 0 });
    } else if (value === 'weighted_score') {
      form.setFieldsValue({
        reranker_id: undefined,
        rerank_weights: { semantic_weight: 0.5, participle_weight: 0.5 },
        enable_graph_retrieval: 0,
      });
    }
  };

  return (
    <div
      ref={layoutContainerRef}
      className={clsx(
        'rb:flex rb:w-full rb:h-full rb:min-w-0 rb:min-h-0 rb:overflow-hidden',
        isWideLayout ? 'rb:flex-row rb:gap-4' : 'rb:flex-col',
      )}
    >
      <div
        className={clsx(
          'rb:min-w-0 rb:min-h-0 rb:overflow-y-auto',
          isWideLayout ? 'rb:flex-1 rb:basis-1/2 rb:max-h-none! rb-border rb:rounded-xl rb:p-3' : 'rb:shrink-0 rb:max-h-[50%]!',
        )}
      >
        <Form form={form} layout="vertical">
          <div
            className={clsx(
              'rb:grid rb:gap-x-4 rb:gap-y-1 rb:mb-3',
              isWideLayout ? 'rb:grid-cols-2' : 'rb:grid-cols-3',
            )}
          >
            <div>
              <Form.Item
                name="retrieve_type"
                label={t('knowledgeBase.retrieveMode')}
                initialValue="hybrid"
                className="rb:col-span-full rb:mb-0!"
              >
                <Select
                  options={retrievalModeOptions}
                  placeholder={t('knowledgeBase.retrieveMode')}
                  onChange={(value) => {
                    if (retrievalPolicy[value]?.includes('image') !== true) {
                      form.setFieldValue('image', undefined);
                    }
                  }}
                />
              </Form.Item>
            </div>

            <div>
              <Form.Item
                name="top_k"
                label={t('knowledgeBase.recallQuantity')}
                className="rb:col-span-full rb:mb-0!"
              >
                <InputNumber
                  placeholder="1 ~ 100"
                  min={1}
                  max={100}
                  className="rb:w-full!"
                />
              </Form.Item>
            </div>

            {/* Show when retrieve_type = semantic or hybrid */}
            {formValues.retrieve_type === 'hybrid' && (
              <>
                <div>
                  <Form.Item
                    name="rerank_mode"
                    label={t('application.rerank_mode')}
                    className="rb:col-span-full rb:mb-0!"
                  >
                    <Select
                      options={[
                        {
                          label: t('application.reranking_model'),
                          value: 'reranking_model',
                        },
                        {
                          label: t('application.weighted_score'),
                          value: 'weighted_score',
                        },
                      ]}
                      placeholder={t('common.pleaseSelect')}
                      onChange={(value) => handleChangeRerankMode(value)}
                    />
                  </Form.Item>
                </div>
                <div className={formValues.rerank_mode !== 'reranking_model' ? 'rb:hidden' : ''}>
                  <Form.Item
                    name="reranker_id"
                    label={t('application.rearrangementModel')}
                    className="rb:col-span-full rb:mb-0!"
                    hidden={formValues.rerank_mode !== 'reranking_model'}
                  >
                    <ModelSelect params={{ type: 'rerank', pagesize: 100 }} />
                  </Form.Item>
                </div>

                {formValues.rerank_mode === 'weighted_score' && (
                  <div>
                    <Form.Item
                      label={t('application.weight_balance')}
                      className="rb:col-span-full rb:mb-0!"
                      required
                    >
                      <WeightBalanceSlider
                        semanticWeight={formValues.rerank_weights?.semantic_weight}
                        participleWeight={formValues.rerank_weights?.participle_weight}
                        onChange={(semanticWeight, participleWeight) => {
                          form.setFieldsValue({
                            rerank_weights: {
                              semantic_weight: semanticWeight,
                              participle_weight: participleWeight,
                            },
                          });
                        }}
                      />
                    </Form.Item>
                    <Form.Item name="rerank_weights" hidden />
                  </div>
                )}

                {formValues.retrieve_type === 'hybrid' &&
                  formValues.rerank_mode === 'reranking_model' && (
                    <div>
                      <Form.Item
                        name="similarity_threshold"
                        label={t('knowledgeBase.similarityThreshold')}
                        className="rb:col-span-full rb:mb-0!"
                      >
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
                    </div>
                  )}
              </>
            )}

            {/* Show when retrieve_type = participle or hybrid */}
            {(formValues.retrieve_type === 'semantic' ||
              (formValues.retrieve_type === 'hybrid' &&
                formValues.rerank_mode === 'reranking_model')) && (
              <div>
                <Form.Item
                  name="vector_similarity_weight"
                  label={t('knowledgeBase.semanticSimilarity')}
                  className="rb:col-span-full rb:mb-0!"
                >
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
              </div>
            )}

            {formValues.retrieve_type === 'hybrid' &&
              formValues.rerank_mode === 'reranking_model' && (
                <div>
                  <Form.Item
                    name="enable_graph_retrieval"
                    getValueProps={(value: 0 | 1 | undefined) => ({ checked: value === 1 })}
                    getValueFromEvent={(checked: boolean) => (checked ? 1 : 0)}
                    initialValue={0}
                    valuePropName="checked"
                    label={t('knowledgeBase.hybridIsHasGraph')}
                    className="rb:col-span-full rb:mb-0!"
                  >
                    <Switch
                      checkedChildren={t('knowledgeBase.yes')}
                      unCheckedChildren={t('knowledgeBase.no')}
                    />
                  </Form.Item>
                </div>
              )}
          </div>
          <Flex align="center" justify="space-between" className="rb:mb-2!">
            <span className="rb:font-medium">{t('knowledgeBase.testQuestion')}</span>
            {/* <Flex align="center" justify="end">
              <img src={refreshIcon} alt="refresh" className="rb:w-4 rb:h-4 rb:mr-2" />
              <span className="rb:text-[#155eef]">{t('knowledgeBase.loadSampleQuestions')}</span>
            </Flex> */}
            <Button type="primary" onClick={handleStartTest} loading={loading}>
              {t('knowledgeBase.startTesting')}
            </Button>
          </Flex>
          <div className="rb:flex rb:flex-col rb:gap-4 rb:sm:flex-row">
            <Form.Item name="query" className="rb:flex-1 rb:mb-0!">
              <TextArea
                rows={4}
                placeholder={t('knowledgeBase.testQuestionPlaceholder')}
                onChange={() => form.setFieldValue('image', undefined)}
              />
            </Form.Item>
            {supportsImage && (
              <Form.Item name="image" className="rb:shrink-0 rb:mb-0!" preserve={false}>
                <RecallImageUpload
                  disabled={loading}
                  onChange={(value) => {
                    if (value) {
                      form.setFieldValue('query', '');
                    }
                  }}
                />
              </Form.Item>
            )}
          </div>
        </Form>
      </div>
      <div
        className={clsx(
          'rb:flex-1 rb:min-w-0 rb:min-h-0 rb:overflow-y-auto',
          isWideLayout && 'rb:basis-1/2 rb-border rb:rounded-xl rb:p-3',
        )}
      >
        <RecallTestResult data={data} showEmpty={true} />
      </div>
    </div>
  );
});

export default RecallTest;
