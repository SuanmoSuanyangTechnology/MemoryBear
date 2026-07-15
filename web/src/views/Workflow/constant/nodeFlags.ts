/* 节点类型标记与触发器配置（拆分自 constant.ts） */
// Nodes with Data Processing in Execution Results
export const hasProcessNodes = [
  'llm',
  'knowledge-retrieval',
  'parameter-extractor',
  'memory-read',
  'memory-write',
  'question-classifier',
  'if-else',
  'assigner',
  'http-request',
  'tool',
  'code',
  'document-extractor',
]
export const hasErrorHandleNodes = [
  'code',
  'http-request',
  'llm',
  'agent'
]
// support single run node
export const cannotRunNodes = [
  'end',
  'output',
]
export const scheduleNodeConfig = {
  cron: {
    type: 'define',
    // required: true,
  },
  // frequency: {
  //   type: 'define',
  //   defaultValue: 'daily'
  // },
  // minute: {
  //   type: 'define',
  //   defaultValue: 0,
  // },
  // time: {
  //   type: 'define',
  //   defaultValue: '12:00 AM',
  // },
  // week_days: {
  //   type: 'define',
  //   defaultValue: []
  // },
  // month_days: {
  //   type: 'define',
  //   defaultValue: []
  // },
}
export const webhookNodeInitConfig = {
  method: {
    type: 'define',
    defaultValue: 'POST'
  },
  route_key: {
    type: 'define',
  },
  content_type: {
    type: 'define',
    defaultValue: 'application/json',
  },
  query_params: {
    type: 'define',
    defaultValue: []
  },
  header_params: {
    type: 'define',
    defaultValue: []
  },
  req_body_params: {
    type: 'define',
    defaultValue: []
  },
  response: {
    type: 'define',
    defaultValue: {
      status_code: 200,
      body: undefined
    }
  }
}
