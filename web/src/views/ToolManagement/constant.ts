import type { InnerConfigItem } from './types';
export const InnerConfigData: Record<string, InnerConfigItem> = {
  DateTimeTool: {
    features: [
      'timeFormat',
      'timeZoneConversion',
      'timestampConversion',
      'timeCalculation'
    ],
  },
  JsonTool: {
    features: [
      'jsonFormat',
      'jsonGzip',
      'jsonCheck',
      'jsonConversion'
    ],
    eg: '{"name":"工具","tool_class":"内置"}'
  },
  BaiduSearchTool: {
    link: 'https://ai.baidu.com/',
    config: {
      api_key: {
        name: ['config', 'parameters', 'api_key'],
        type: 'input',
        desc: 'BaiduSearchTool_api_key_desc',
        rules: [
          { required: true, message: 'common.pleaseEnter' }
        ]
      },
      type: {
        name: ['config', 'parameters', 'search_type'],
        type: 'select',
        options: [
          { label: 'webSearch', value: 'web' },
          { label: 'newsSearch', value: 'news' },
          { label: 'imageSearch', value: 'image' },
        ],
        defaultValue: 'webSearch'
      },
      pagesize: {
        name: ['config', 'parameters', 'pagesize'],
        type: 'number',
        range: {
          web: [1, 50],
          news: [1, 30],
          image: [1, 10],
        },
        step: 1,
        defaultValue: 10,
        desc: 'pagesize_desc'
      },
      BaiduSearchTool_enable: {
        name: ['config', 'is_enabled'],
        type: 'checkbox',
        defaultValue: true,
      },
    },
    features: [
      'webSearch',
      'newsSearch',
      'imageSearch',
      'realTimeResults'
    ],
  },
  MinerUTool: {
    link: 'https://MinerUTool.ai/',
    config: {
      api_key: {
        name: ['config', 'parameters', 'api_key'],
        type: 'input',
        desc: 'MinerUTool_api_key_desc',
        rules: [
          { required: true, message: 'common.pleaseEnter' }
        ]
      },
      api_address: {
        name: ['config', 'parameters', 'api_address'],
        type: 'input',
        desc: 'MinerUTool_api_address_desc',
        defaultValue: 'https://api.MinerUTool.ai/v1'
      },
      parsing_mode: {
        name: ['config', 'parameters', 'parsing_mode'],
        type: 'select',
        options: [
          { label: 'auto_recognition', value: 'auto_recognition' },
          { label: 'pure_text_mode', value: 'pure_text_mode' },
          { label: 'table_priority', value: 'table_priority' },
          { label: 'image_priority', value: 'image_priority' },
        ],
        defaultValue: 'auto_recognition'
      },
      timeout: {
        name: ['config', 'parameters', 'timeout'],
        type: 'number',
        min: 10,
        max: 300,
        step: 1,
        defaultValue: 60,
        desc: 'MinerUTool_timeout_desc'
      },
      MinerUTool_enable: {
        name: ['config', 'is_enabled'],
        type: 'checkbox',
        defaultValue: true,
      },
      MinerUTool_extract_images_enable: {
        name: ['config', 'images_enable'],
        type: 'checkbox',
        defaultValue: true,
        desc: 'MinerUTool_extract_images_enable_desc'
      }
    },
    features: [
      'pdfParser',
      'tableExtraction',
      'imageRecognition',
      'textExtraction'
    ],
  },
  TextInTool: {
    link: 'https://www.TextInTool.com/',
    config: {
      app_id: {
        name: ['config', 'parameters', 'app_id'],
        type: 'input',
        desc: 'TextInTool_app_id_desc',
        rules: [
          { required: true, message: 'common.pleaseEnter' }
        ]
      },
      secret_key: {
        name: ['config', 'parameters', 'secret_key'],
        type: 'input',
        desc: 'TextInTool_secret_key_desc',
        rules: [
          { required: true, message: 'common.pleaseEnter' }
        ]
      },
      api_address: {
        name: ['config', 'parameters', 'api_address'],
        type: 'input',
        desc: 'TextInTool_api_address_desc',
        defaultValue: 'https://api.MinerUTool.ai/v1'
      },
      language_identification: {
        name: ['config', 'parameters', 'language_identification'],
        type: 'select',
        options: [
          { label: 'automatic_detection', value: 'automatic_detection' },
          { label: 'simplified_chinese', value: 'simplified_chinese' },
          { label: 'traditional_chinese', value: 'traditional_chinese' },
          { label: 'english', value: 'english' },
          { label: 'japanese', value: 'japanese' },
          { label: 'korean_language', value: 'korean_language' },
        ],
        defaultValue: 'automatic_detection'
      },
      pattern_recognition: {
        name: ['config', 'parameters', 'pattern_recognition'],
        type: 'select',
        options: [
          { label: 'universal_identification', value: 'universal_identification' },
          { label: 'high_precision_identification', value: 'high_precision_identification' },
          { label: 'handwriting_recognition', value: 'handwriting_recognition' },
          { label: 'formula_recognition', value: 'formula_recognition' },
        ],
        defaultValue: 'universal_identification'
      },
      TextInTool_enable: {
        name: ['config', 'is_enabled'],
        type: 'checkbox',
        defaultValue: true,
      },
      return_text_position_enable: {
        name: ['config', 'position_enable'],
        type: 'checkbox',
        defaultValue: true,
        desc: 'return_text_position_enable_desc'
      },
    },
    features: [
      'universalOCR',
      'handwritingRecognition',
      'multilingualSupport',
      'highPrecisionRecognition'
    ],
  }
}