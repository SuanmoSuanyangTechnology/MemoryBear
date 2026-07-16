/* 模型参数配置（拆分自 constant.ts） */
export const modelConfig: Record<string, any> = {
  model_id: {
    type: 'define',
    required: true,
    params: { type: 'llm,chat' }, // llm/chat
    valueKey: 'id',
    labelKey: 'name',
  },
  temperature: {
    type: 'define',
    defaultValue: 0.7
  },
  max_tokens: { 
    type: 'define',
    defaultValue: 8000 
  },
  json_output: {
    type: 'define',
    defaultValue: false
  },
  structured_output: {
    type: 'define',
    defaultValue: false
  },
  json_output_fields: {
    type: 'define',
  },
  // Top P 采样参数
  top_p: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0.8
    }
  },
  // 取样数量
  top_k: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 50
    }
  },
  // 随机种子
  seed: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 1234
    }
  },
  // 重复惩罚
  repetition_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 1.0
    }
  },
  // 联网搜索
  // enable_search: {
  //   type: 'define',
  //   defaultValue: false
  // },
  // 思考模式
  thinking: {
    type: 'define',
    defaultValue: {
      budget: {
          enable: false,
          value: 256
      },
      enable: false
    }
  },
  // 回复格式
  response_format: {
    type: 'define',
    options: [
      { label: 'text', value: 'text' },
      { label: 'json_object', value: 'json_object' },
    ],
    defaultValue: 'text',
  },
  // 额外请求头，字符串格式
  extra_headers: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: undefined
    }
  },
  // 停止序列, 输入序列并按 Tab 键
  stop: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: []
    }
  },
  // 存在惩罚
  presence_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0
    }
  },
  // 频率惩罚
  frequency_penalty: {
    type: 'define',
    defaultValue: {
      enable: false,
      value: 0
    }
  }
}
