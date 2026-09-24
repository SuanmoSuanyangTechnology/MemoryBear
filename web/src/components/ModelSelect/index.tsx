/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-07 16:49:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-18 12:10:55
 */
import { useEffect, useState, useMemo } from 'react';
import { Select, Flex } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import type { SelectProps } from 'antd/es/select';
import { useTranslation } from 'react-i18next';

import { getModelList, getModelInfo } from '@/api/models';
import type { Query, Model } from '@/views/ModelManagement/types';
import { getListLogoUrl } from '@/views/ModelManagement/utils';
import ModelAbilityTags from '@/components/ModelAbilityTags';
import ModelStatusTag from './ModelStatusTag'

/** Extends AntD SelectProps; omits filterOption since it's handled internally */
type ModelOption = Pick<Model, 'id' | 'name' | 'provider' | 'logo' | 'type' | 'is_deprecated'> & Partial<Pick<Model, 'is_available' | 'input_modalities' | 'output_modalities' | 'features'>>;

interface ModelSelectProps<T extends ModelOption = Model> extends SelectProps {
  valueKey?: 'id' | 'name';
  disableDeprecated?: boolean;
  /** Extra query params passed to getModelList */
  params?: Query;
  inputModality?: string;
  placeholder?: string;
  fontClassName?: string;
  isAutoFetch?: boolean;
  initialData?: T[];
  updateOptions?: (options: T[]) => void;
}

const ModelSelect = <T extends ModelOption = Model,>({ params, inputModality, placeholder, fontClassName, isAutoFetch = true, initialData = [], updateOptions, valueKey = 'id', disableDeprecated = true, ...props }: ModelSelectProps<T>) => {
  const { t } = useTranslation();
  const [options, setOptions] = useState<T[]>([]);
  const [optionsLoaded, setOptionsLoaded] = useState(!isAutoFetch);
  const [selectedModel, setSelectedModel] = useState<T>();

  const allOptions = useMemo(
    () => [...options, ...initialData],
    [JSON.stringify(options), JSON.stringify(initialData)]
  );
  // Fetch active models whenever params change; stringify for stable deep comparison
  useEffect(() => {
    if (!isAutoFetch) {
      setOptionsLoaded(true);
      return;
    }
    setOptionsLoaded(false);
    getModelList({
      ...(params ?? {}),
      pagesize: 100,
      is_available: true,
    }).then((res) => {
      setOptions((res as { items: T[] }).items ?? []);
    }).finally(() => {
      setOptionsLoaded(true);
    });
  }, [JSON.stringify(params), isAutoFetch]);

  const selectedValue = useMemo(() => {
    return Array.isArray(props.value)
      ? undefined
      : typeof props.value === 'object' && props.value !== null && 'value' in props.value
        ? props.value.value
        : props.value;
  }, [props.value])

  useEffect(() => {
    const modelId = typeof selectedValue === 'string' ? selectedValue : undefined;
    if (!optionsLoaded || !modelId || allOptions.some((item) => item.id === modelId)) {
      setSelectedModel(undefined);
      return;
    }

    let cancelled = false;
    getModelInfo(modelId)
      .then((res) => {
        if (!cancelled) setSelectedModel(res as T);
      })
      .catch(() => {
        if (!cancelled) setSelectedModel(undefined);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedValue, optionsLoaded, allOptions]);

  // Render the selected value inside the trigger with logo + truncated name
  const labelRender: SelectProps['labelRender'] = ({ value }) => {
    const item = allOptions.find((o) => o[valueKey] === value) ?? (selectedModel?.[valueKey] === value ? selectedModel : undefined);
    if (!item) return value;
    const logo = getListLogoUrl(item.provider, item.logo as string);
    return (
      <Flex align="center" gap={8}>
        {logo && <img src={logo} className="rb:size-5 rb:rounded-md" alt={logo} />}
        <div className={`rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap ${fontClassName}`}>{item.name}</div>
        <ModelStatusTag model={item as unknown as Model} />
      </Flex>
    );
  };

  const multipleTagRender: SelectProps['tagRender'] = (tagProps) => {
    const { label, disabled, closable, onClose, isMaxTag } = tagProps;
    const canClose = !props.disabled && !isMaxTag && (closable || disabled);

    if (props.tagRender) {
      return props.tagRender({ ...tagProps, closable: canClose });
    }

    return (
      <span
        title={typeof label === 'string' || typeof label === 'number' ? String(label) : undefined}
        className="ant-select-selection-item"
        onMouseDown={(event) => {
          event.preventDefault();
          event.stopPropagation();
        }}
      >
        <span className="ant-select-selection-item-content">{label}</span>
        {canClose && (
          <span
            className="ant-select-selection-item-remove"
            onClick={(event) => onClose?.(event)}
          >
            <CloseOutlined />
          </span>
        )}
      </span>
    );
  };

  useEffect(() => {
    if (updateOptions) updateOptions(allOptions);
  }, [JSON.stringify(allOptions), updateOptions])


  return (
    <Select
      placeholder={placeholder ?? t('common.pleaseSelect')}
      options={allOptions.filter(item => !inputModality || item.input_modalities?.some(value => value === inputModality))
        .map(item => ({
          ...item,
          disabled: (item && typeof item.is_available !== 'undefined' && !item.is_available) || disableDeprecated && item.is_deprecated
        })
      )}
      fieldNames={{ label: 'name', value: valueKey }}
      // optionFilterProp="name"
      allowClear
      // popupMatchSelectWidth={false}
      labelRender={labelRender}
      // Each dropdown option shows logo, name, and capability tags
      optionRender={(option) => {
        const { data } = option;
        const logo = getListLogoUrl(data.provider, data.logo as string);
        return (
          <Flex vertical gap={8} className={(data.is_deprecated || (data && typeof data.is_available !== 'undefined' && !data.is_available)) ? 'rb:opacity-65' : '' }>
            <Flex align="center" gap={8}>
              {logo && <img src={logo} className="rb:size-5 rb:rounded-md" alt={logo} />}
              <span className="rb:wrap-break-word rb:line-clamp-1">{data.name as string}</span>
              <ModelStatusTag model={data as unknown as Model} />
            </Flex>
            <ModelAbilityTags {...(data as Model)} />
          </Flex>
        );
      }}
      {...props}
      tagRender={props.mode === 'multiple' ? multipleTagRender : props.tagRender}
    />
  );
};

export default ModelSelect;
