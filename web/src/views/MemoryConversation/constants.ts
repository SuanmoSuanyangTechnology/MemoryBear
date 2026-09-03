const SPLIT_MEMORY_STAGES = [
  'requestMode',
  'queryPreprocess',
  'problemSplit',
  'userMetadata',
  'hybridRetrieval',
  'scoreMerge',
  'finalAnswer',
] as const

const DIRECT_MEMORY_STAGES = [
  'requestMode',
  'queryPreprocess',
  'userMetadata',
  'hybridRetrieval',
  'scoreMerge',
  'finalAnswer',
] as const

export type MemoryStageKey = typeof SPLIT_MEMORY_STAGES[number]

export const getMemoryStages = (searchSwitch: string): readonly MemoryStageKey[] => (
  ['0', '1'].includes(searchSwitch) ? SPLIT_MEMORY_STAGES : DIRECT_MEMORY_STAGES
)

export const MEMORY_STAGE_ALIASES = {
  start: 'requestMode',
  request: 'requestMode',
  request_mode: 'requestMode',
  read_mode: 'requestMode',
  tool_start: 'requestMode',
  query: 'queryPreprocess',
  query_preprocess: 'queryPreprocess',
  query_preprocessed: 'queryPreprocess',
  query_preprocessing: 'queryPreprocess',
  preprocess: 'queryPreprocess',
  query_split: 'problemSplit',
  problem_split: 'problemSplit',
  profile_loaded: 'userMetadata',
  user_metadata: 'userMetadata',
  metadata: 'userMetadata',
  l0: 'userMetadata',
  hybrid_searched: 'hybridRetrieval',
  keyword_searched: 'hybridRetrieval',
  hybrid_retrieval: 'hybridRetrieval',
  retrieval: 'hybridRetrieval',
  memory_stage: 'hybridRetrieval',
  retrieval_trace: 'scoreMerge',
  score_merge: 'scoreMerge',
  result_ready: 'scoreMerge',
  merge: 'scoreMerge',
  rerank: 'scoreMerge',
  search_result: 'finalAnswer',
  message: 'finalAnswer',
  end: 'finalAnswer',
  final_answer: 'finalAnswer',
  summary: 'finalAnswer',
  answer: 'finalAnswer',
} satisfies Record<string, MemoryStageKey>

export const SEARCH_MODE = '2'

export const STREAM_EVENTS = {
  START: 'start',
  MEMORY_STAGE: 'memory_stage',
  RETRIEVAL_TRACE: 'retrieval_trace',
  MESSAGE: 'message',
  END: 'end',
} as const
