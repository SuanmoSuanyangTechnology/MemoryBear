/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:30:06 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-04 10:09:45
 */
/**
 * Memory Extraction Engine Configuration Constants
 * Defines configuration structure for storage and arrangement layer modules
 */

import type { ConfigVo } from './types'

/** Configuration list for memory extraction engine */
export const configList: ConfigVo[] = [
  {
    type: 'storageLayerModule',
    data: [
      {
        title: 'entityDeduplicationDisambiguation',
        list: [
          {
            label: 'enableLlmDedupBlockwise',
            variableName: 'enable_llm_dedup_blockwise',
            control: 'button', // switch
            type: 'tinyint',
          },
          {
            label: 'enableLlmDisambiguation',
            variableName: 'enable_llm_disambiguation',
            control: 'button',
            type: 'tinyint',
          },
          {
            label: 'tNameStrict',
            control: 'slider',
            variableName: 't_name_strict',
            type: 'decimal',
          },
          {
            label: 'tTypeStrict',
            control: 'slider',
            variableName: 't_type_strict',
            type: 'decimal',
          },
          {
            label: 'tOverall',
            control: 'slider',
            variableName: 't_overall',
            type: 'decimal',
          },
        ]
      },
      // Semantic anchor annotation
      {
        title: 'semanticAnchorAnnotationModule',
        list: [
          // Sentence extraction granularity
          {
            label: 'statementGranularity',
            variableName: 'statement_granularity',
            control: 'slider',
            type: 'decimal',
            max: 3,
            min: 1,
            step: 1,
            meaning: 'statementGranularityDesc',
          },
          // Include dialogue context
          {
            label: 'includeDialogueContext',
            variableName: 'include_dialogue_context',
            control: 'button', // switch
            type: 'tinyint',
            meaning: 'includeDialogueContextDesc'
          },
          // Context text limit
          {
            label: 'maxDialogueContextChars',
            variableName: 'max_context',
            control: 'inputNumber',
            min: 100,
            type: 'decimal',
            meaning: 'maxDialogueContextCharsDesc',
          },
        ]
      },
    ]
  },
  {
    type: 'arrangementLayerModule',
    data: [
      {
        title: 'queryMode',
        list: [
          {
            label: 'deepRetrieval',
            variableName: 'deep_retrieval',
            control: 'button',
            type: 'tinyint',
            meaning: 'deepRetrievalMeaning',
          },
        ]
      },
      {
        title: 'dataPreprocessing',
        list: [
          {
            label: 'chunkerStrategy',
            variableName: 'chunker_strategy',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'recursiveChunker', value: 'RecursiveChunker' }, // Recursive chunking
              { label: 'tokenChunker', value: 'TokenChunker' }, // Token chunking
              { label: 'semanticChunker', value: 'SemanticChunker' }, // Semantic chunking
              { label: 'neuralChunker', value: 'NeuralChunker' }, // Neural network chunking
              { label: 'hybridChunker', value: 'HybridChunker' }, // Hybrid chunking
              { label: 'llmChunker', value: 'LLMChunker' }, // LLM chunking
              { label: 'sentenceChunker', value: 'SentenceChunker' }, // Sentence chunking
              { label: 'lateChunker', value: 'LateChunker' }, // Late chunking
            ],
            meaning: 'chunkerStrategyDesc',
          },
        ]
      },
      // Intelligent semantic pruning
      {
        title: 'intelligentSemanticPruning',
        list: [
          // Intelligent semantic pruning功能
          {
            label: 'intelligentSemanticPruningFunction',
            variableName: 'pruning_enabled',
            control: 'button',
            type: 'tinyint',
            meaning: 'intelligentSemanticPruningFunctionDesc',
          },
          // Intelligent semantic pruning场景
          {
            label: 'intelligentSemanticPruningScene',
            variableName: 'pruning_scene',
            control: 'select',
            type: 'enum',
            options: [
              { label: 'education', value: 'education' },
              { label: 'online_service', value: 'online_service' },
              { label: 'outbound', value: 'outbound' },
            ],
            meaning: 'intelligentSemanticPruningSceneDesc',
          },
          // Intelligent semantic pruning阈值
          {
            label: 'intelligentSemanticPruningThreshold',
            control: 'slider',
            variableName: 'pruning_threshold',
            type: 'decimal',
            max: 0.9,
            min: 0,
            step: 0.1,
            meaning: 'intelligentSemanticPruningThresholdDesc',
          },
        ]
      },
      // Self-reflection engine
      // {
      //   title: 'reflectionEngine',
      //   list: [
      //     // Enable reflection engine
      //     {
      //       label: 'enableSelfReflexion',
      //       variableName: 'enable_self_reflexion',
      //       control: 'button',
      //       type: 'tinyint',
      //     },
      //     // Iteration period
      //     {
      //       label: 'iterationPeriod',
      //       variableName: 'iteration_period',
      //       control: 'select',
      //       type: 'enum',
      //       options: [
      //         { label: 'oneHour', value: '1' },
      //         { label: 'threeHours', value: '3' },
      //         { label: 'sixHours', value: '6' },
      //         { label: 'twelveHours', value: '12' },
      //         { label: 'daily', value: '24' },
      //       ],
      //       meaning: 'iterationPeriodDesc',
      //     },
      //     // Reflection range
      //     {
      //       label: 'reflexionRange',
      //       variableName: 'reflexion_range',
      //       control: 'select',
      //       type: 'enum',
      //       options: [
      //         { label: 'retrieval', value: 'retrieval' },
      //         { label: 'database', value: 'database' },
      //       ],
      //       meaning: 'reflexionRangeDesc',
      //     },
      //     // Reflection baseline
      //     {
      //       label: 'reflectOnTheBaseline',
      //       variableName: 'baseline',
      //       control: 'select',
      //       type: 'enum',
      //       options: [
      //         { label: 'basedOnTime', value: 'TIME' },
      //         { label: 'basedOnFacts', value: 'FACT' },
      //         { label: 'basedOnFactsAndTime', value: 'TIME-FACT' },
      //       ],
      //     },
      //   ]
      // },
    ]
  }
]

/**
 * Group data by specified key
 * @param data - Array of data items
 * @param groupKey - Key to group by
 * @returns Grouped data object
 */
export const groupDataByType = (data: any[], groupKey: string) => {
  const grouped: { [key: string]: any[] } = {}
  
  data.forEach(item => {
    if (item[groupKey]) {
      if (!grouped[item[groupKey]]) {
        grouped[item[groupKey]] = []
      }
      grouped[item[groupKey]].push(item)
    } else {
      if (!grouped.unknown) {
        grouped.unknown = []
      }
      grouped.unknown.push(item)
    }
  })
  
  return grouped
}