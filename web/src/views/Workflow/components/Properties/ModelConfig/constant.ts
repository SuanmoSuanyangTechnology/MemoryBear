import type { JsonSchema, Field } from './types'
export const fieldConfigs: Record<string, any> = {
  temperature: {
    type: 'slider',
    max: 1.99, 
    min: 0, 
    step: 0.1,
    defaultValue: 0.7
  },
  max_tokens: {
    type: 'slider',
    max: 32000, 
    min: 256, 
    step: 1, 
    defaultValue: 8000 
  },
  json_output: {
    type: 'switch',
    dependence: 'capability',
    defaultValue: false,
    hideTip: true
  },
  structured_output: {
    type: 'switch',
    dependence: 'capability',
    defaultValue: false,
    hideTip: true
  },
  json_output_fields: {
    type: 'editor',
    dependence: 'capability',
  },
  top_p: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'slider',
      min: 0.1,
      max: 1,
      step: 0.1,
      defaultValue: 0.8
    }
  },
  top_k: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'slider',
      min: 1,
      max: 100,
      step: 1,
      defaultValue: 50
    }
  },
  seed: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'inputNumber',
      min: 0,
      max: 18446744073709551615,
      defaultValue: 1234
    }
  },
  repetition_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'inputNumber',
      min: 0.1,
      max: 2,
      step: 0.1,
      defaultValue: 1.0
    }
  },
  // enable_search: {
  //   type: 'switch',
  //   defaultValue: false
  // },
  thinking: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    budget: {
      enable: {
        type: 'switch',
        defaultValue: false
      },
      value: {
        type: 'inputNumber',
        min: 128,
        defaultValue: 256
      }
    }
  },
  response_format: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'select',
      options: [
        { label: 'text', value: 'text' },
        { label: 'json_object', value: 'json_object' },
      ],
      defaultValue: 'text',
    }
  },
  extra_headers: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'editor',
    }
  },
  stop: {
    enable: {
      type: 'switch',
      defaultValue: false
    },
    value: {
      type: 'select',
      mode: 'tags',
      maxTagCount: 4,
      defaultValue: []
    }
  },
  presence_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false,
      hideTip: true
    },
    value: {
      type: 'inputNumber',
      min: -2,
      max: 2,
      step: 0.1,
      defaultValue: 0
    }
  },
  frequency_penalty: {
    enable: {
      type: 'switch',
      defaultValue: false,
      hideTip: true
    },
    value: {
      type: 'inputNumber',
      min: -2,
      max: 2,
      step: 0.1,
      defaultValue: 0
    }
  }
}

export const defaultJsonSchema: JsonSchema = []

export const typeOptions = [
  { value: 'string', label: 'string' },
  { value: 'number', label: 'number' },
  { value: 'boolean', label: 'boolean' },
  { value: 'object', label: 'object' },
  { value: 'array[string]', label: 'array[string]' },
  { value: 'array[number]', label: 'array[number]' },
  { value: 'array[object]', label: 'array[object]' },
];

// 默认展开所有 object 类型节点的 key
export const getAllObjectKeys = (list: Field[], path: number[] = []): string[] => {
  const keys: string[] = [];
  list.forEach((field, index) => {
    const currentPath = [...path, index];
    if (field.type.includes('object')) {
      keys.push(currentPath.join(','));
      if (field.children) {
        keys.push(...getAllObjectKeys(field.children, currentPath));
      }
    }
  });
  return keys;
};