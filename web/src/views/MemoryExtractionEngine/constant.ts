import type { ConfigVo } from './types'

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
      // 语义锚点标注
      {
        title: 'semanticAnchorAnnotationModule',
        list: [
          // 句子提取颗粒度
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
          // 是否包含对话上下文
          {
            label: 'includeDialogueContext',
            variableName: 'include_dialogue_context',
            control: 'button', // switch
            type: 'tinyint',
            meaning: 'includeDialogueContextDesc'
          },
          // 上下文文字上限
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
              { label: 'recursiveChunker', value: 'RecursiveChunker' }, // 递归分块
              { label: 'tokenChunker', value: 'TokenChunker' }, // token 分块
              { label: 'semanticChunker', value: 'SemanticChunker' }, // 语义分块
              { label: 'neuralChunker', value: 'NeuralChunker' }, // 神经网络分块
              { label: 'hybridChunker', value: 'HybridChunker' }, // 混合分块
              { label: 'llmChunker', value: 'LLMChunker' }, // LLM 分块
              { label: 'sentenceChunker', value: 'SentenceChunker' }, // 句子分块
              { label: 'lateChunker', value: 'LateChunker' }, // 延迟分块
            ],
            meaning: 'chunkerStrategyDesc',
          },
        ]
      },
      // 智能语义剪枝
      {
        title: 'intelligentSemanticPruning',
        list: [
          // 智能语义剪枝功能
          {
            label: 'intelligentSemanticPruningFunction',
            variableName: 'pruning_enabled',
            control: 'button',
            type: 'tinyint',
            meaning: 'intelligentSemanticPruningFunctionDesc',
          },
          // 智能语义剪枝场景
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
          // 智能语义剪枝阈值
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
      // 自我反思引擎
      // {
      //   title: 'reflectionEngine',
      //   list: [
      //     // 是否启用反思引擎
      //     {
      //       label: 'enableSelfReflexion',
      //       variableName: 'enable_self_reflexion',
      //       control: 'button',
      //       type: 'tinyint',
      //     },
      //     // 迭代周期
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
      //     // 反思范围
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
      //     // 反思基线
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

export const mockModuleData = [
    {
        "data": [
            {
                "chunk_index": 1,
                "content": "用户: 学生：宋朝军事弱势的根源是什么？\n记忆熊：宋朝军事弱势主要源于制度性因素：禁军由文官控制导致作战效率低下，厢军装备不足；军费长期占财政支出一半以上，持续战争加剧财政压力，反向制约军力发展；对外战略被动，缺乏战略纵深且机动能力弱；文官集团压制武将，军权分散，军事专业化难以提升。\n\n学生：我对财政压力与军力不足的关系表示困惑。\n记忆熊：简单说，宋朝军费占财政一半以上，钱越花越多，仗却越打越输。...",
                "full_length": 416,
                "dialog_id": "7d11cacd843d45e5942a3ebdc7eb5c8a",
                "chunker_strategy": "RecursiveChunker"
            },
            {
                "chunk_index": 2,
                "content": "\n学生：我对\"六部缺乏协调机制\"的具体影响表示理解不足。\n记忆熊：原来丞相相当于\"总理\"，六部像今天的各部委，大事小情由他统筹。废丞相后，六部直接对皇帝，皇帝一人批不完，部就互相踢皮球。比如打仗，兵部要银子，户部说没有，工部说武器没材料，没人拍板，战事就拖延。\n\n学生：我在复习春秋战国史，对秦国为何能统一六国表示疑问，认为其他国家也较强。\n记忆熊：秦国统一的原因包括：商鞅变法彻底，建立法律、户籍和...",
                "full_length": 428,
                "dialog_id": "7d11cacd843d45e5942a3ebdc7eb5c8a",
                "chunker_strategy": "RecursiveChunker"
            }
        ],
        "status": "completed",
        "result": {
            "total_chunks": 2,
            "total_dialogs": 1,
            "chunker_strategy": "RecursiveChunker"
        }
    },
    {
        "data": [
            {
                "extraction_type": "statement",
                "statement_index": 1,
                "statement": "记忆熊认为宋朝军事弱势主要源于制度性因素。",
                "statement_id": "dc0e8b331e584525bda5b63beece6449"
            },
            {
                "extraction_type": "statement",
                "statement_index": 2,
                "statement": "记忆熊指出禁军由文官控制导致作战效率低下。",
                "statement_id": "b60c2c4cbbc3469a8eccf63eaff8af7c"
            },
            {
                "extraction_type": "statement",
                "statement_index": 3,
                "statement": "记忆熊指出厢军装备不足。",
                "statement_id": "56b912b3424c41c582849ea47f3c9a67"
            },
            {
                "extraction_type": "statement",
                "statement_index": 4,
                "statement": "记忆熊指出宋朝军费长期占财政支出一半以上。",
                "statement_id": "6e9f5a974b864731b4f45b156ee2b2b9"
            },
            {
                "extraction_type": "statement",
                "statement_index": 5,
                "statement": "记忆熊指出持续战争加剧财政压力，反向制约军力发展。",
                "statement_id": "4fbcf48493fa40cd97d2e758046a8114"
            },
            {
                "extraction_type": "statement",
                "statement_index": 6,
                "statement": "记忆熊指出宋朝对外战略被动，缺乏战略纵深且机动能力弱。",
                "statement_id": "672bb8a4aac548a481ab3c6866ff1537"
            },
            {
                "extraction_type": "statement",
                "statement_index": 7,
                "statement": "记忆熊指出文官集团压制武将，军权分散，军事专业化难以提升。",
                "statement_id": "94f51d5939d440a89600cc1fede8203e"
            },
            {
                "extraction_type": "statement",
                "statement_index": 8,
                "statement": "学生对财政压力与军力不足的关系表示困惑。",
                "statement_id": "74304297767144fb98e1f28de4397eba"
            },
            {
                "extraction_type": "statement",
                "statement_index": 9,
                "statement": "记忆熊解释宋朝军费占财政一半以上，钱越花越多，仗却越打越输。",
                "statement_id": "52169673071844d58cc475f350e0e878"
            },
            {
                "extraction_type": "statement",
                "statement_index": 10,
                "statement": "记忆熊指出财政被军费拖垮后，朝廷只能削减装备、裁撤兵员。",
                "statement_id": "92c5b675666a444d8bba605682376018"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 1,
                "subject": "记忆熊",
                "predicate": "MENTIONS",
                "object": "宋朝军事弱势"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 2,
                "subject": "宋朝军事弱势",
                "predicate": "RESULTED_IN",
                "object": "制度性因素"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 3,
                "subject": "记忆熊",
                "predicate": "MENTIONS",
                "object": "禁军由文官控制导致作战效率低下"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 4,
                "subject": "禁军由文官控制",
                "predicate": "RESULTED_IN",
                "object": "作战效率低下"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 5,
                "subject": "记忆熊",
                "predicate": "MENTIONS",
                "object": "厢军装备不足"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 6,
                "subject": "记忆熊",
                "predicate": "MENTIONS",
                "object": "宋朝"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 7,
                "subject": "记忆熊",
                "predicate": "MENTIONS",
                "object": "军费"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 8,
                "subject": "军费",
                "predicate": "HAS_A",
                "object": "财政支出"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 9,
                "subject": "宋朝",
                "predicate": "HAS_REVENUE",
                "object": "财政支出"
            },
            {
                "extraction_type": "triplet",
                "triplet_index": 10,
                "subject": "持续战争",
                "predicate": "RESULTED_IN",
                "object": "财政压力"
            },
            {
                "extraction_type": "temporal",
                "temporal_index": 1,
                "statement": "记忆熊认为宋朝军事弱势主要源于制度性因素。",
                "valid_at": null,
                "invalid_at": null
            },
            {
                "extraction_type": "temporal",
                "temporal_index": 2,
                "statement": "记忆熊指出禁军由文官控制导致作战效率低下。",
                "valid_at": null,
                "invalid_at": null
            },
            {
                "extraction_type": "temporal",
                "temporal_index": 3,
                "statement": "记忆熊指出厢军装备不足。",
                "valid_at": null,
                "invalid_at": null
            },
            {
                "extraction_type": "temporal",
                "temporal_index": 4,
                "statement": "记忆熊指出宋朝军费长期占财政支出一半以上。",
                "valid_at": null,
                "invalid_at": null
            },
            {
                "extraction_type": "temporal",
                "temporal_index": 5,
                "statement": "记忆熊指出持续战争加剧财政压力，反向制约军力发展。",
                "valid_at": null,
                "invalid_at": null
            }
        ],
        "status": "completed",
        "result": {
            "statements": {
                "count": 38
            },
            "entities": {
                "count": 148
            },
            "triplets": {
                "count": 88
            },
            "temporal_ranges_count": 38
        }
    },
    {
        "data": [
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Person",
                "type_display_name": "人物实体节点",
                "entity_names": [
                    "记忆熊",
                    "记忆熊",
                    "记忆熊",
                    "记忆熊",
                    "记忆熊",
                    "学生",
                    "记忆熊",
                    "学生",
                    "记忆熊",
                    "丞相",
                    "..."
                ],
                "total_count": 21
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Phenomenon",
                "type_display_name": "Phenomenon实体节点",
                "entity_names": [
                    "宋朝军事弱势"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Factor",
                "type_display_name": "Factor实体节点",
                "entity_names": [
                    "制度性因素"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Policy",
                "type_display_name": "政策实体节点",
                "entity_names": [
                    "禁军由文官控制",
                    "商鞅变法"
                ],
                "total_count": 2
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Condition",
                "type_display_name": "条件实体节点",
                "entity_names": [
                    "作战效率低下",
                    "厢军装备不足",
                    "军力发展受制约",
                    "军权分散",
                    "军事专业化难以提升",
                    "官僚体系僵化",
                    "缺乏协作机制",
                    "缺乏协调机制",
                    "难以支撑军队"
                ],
                "total_count": 9
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Statement",
                "type_display_name": "Statement实体节点",
                "entity_names": [
                    "禁军由文官控制导致作战效率低下",
                    "没有银子",
                    "武器没材料"
                ],
                "total_count": 3
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Organization",
                "type_display_name": "组织实体节点",
                "entity_names": [
                    "宋朝",
                    "宋朝",
                    "文官集团",
                    "宋朝",
                    "朝廷",
                    "官僚体系",
                    "六部",
                    "厂卫机构",
                    "锦衣卫",
                    "东厂",
                    "..."
                ],
                "total_count": 25
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "EconomicMetric",
                "type_display_name": "EconomicMetric实体节点",
                "entity_names": [
                    "军费",
                    "财政支出",
                    "财政",
                    "军费",
                    "支出"
                ],
                "total_count": 5
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Event",
                "type_display_name": "事件实体节点",
                "entity_names": [
                    "持续战争",
                    "削减装备和裁撤兵员",
                    "战事失利",
                    "废除丞相制度",
                    "废除丞相制度",
                    "废除丞相制度",
                    "大事小情",
                    "废除丞相制度",
                    "无法批阅完所有政务",
                    "政令执行困难",
                    "..."
                ],
                "total_count": 15
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "EconomicFactor",
                "type_display_name": "经济因素实体节点",
                "entity_names": [
                    "财政压力"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "EconomicIndicator",
                "type_display_name": "EconomicIndicator实体节点",
                "entity_names": [
                    "财政支出"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "MilitaryStrategy",
                "type_display_name": "MilitaryStrategy实体节点",
                "entity_names": [
                    "对外战略被动"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "MilitaryCondition",
                "type_display_name": "MilitaryCondition实体节点",
                "entity_names": [
                    "缺乏战略纵深",
                    "军力不足"
                ],
                "total_count": 2
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "MilitaryCapability",
                "type_display_name": "MilitaryCapability实体节点",
                "entity_names": [
                    "机动能力弱"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "PersonGroup",
                "type_display_name": "PersonGroup实体节点",
                "entity_names": [
                    "武将"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Concept",
                "type_display_name": "概念实体节点",
                "entity_names": [
                    "财政压力",
                    "军力不足",
                    "恶性循环",
                    "行政紧张",
                    "系统行政训练",
                    "专业分工",
                    "六部缺乏协调机制",
                    "六部缺乏协调机制",
                    "秦国统一六国的原因"
                ],
                "total_count": 9
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "EconomicCondition",
                "type_display_name": "EconomicCondition实体节点",
                "entity_names": [
                    "财政压力"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Action",
                "type_display_name": "Action实体节点",
                "entity_names": [
                    "削减装备",
                    "裁撤兵员",
                    "再花钱募兵",
                    "建立法律制度",
                    "建立户籍制度",
                    "建立军功爵制度"
                ],
                "total_count": 6
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "State",
                "type_display_name": "State实体节点",
                "entity_names": [
                    "军队更弱",
                    "不足",
                    "理解不足"
                ],
                "total_count": 3
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Outcome",
                "type_display_name": "Outcome实体节点",
                "entity_names": [
                    "打仗更吃亏",
                    "降低行政效率",
                    "政令推行困难",
                    "提升国家组织能力",
                    "士兵效忠个人而非国家"
                ],
                "total_count": 5
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "HistoricalPeriod",
                "type_display_name": "历史时期实体节点",
                "entity_names": [
                    "宋朝",
                    "春秋战国史",
                    "唐朝史"
                ],
                "total_count": 3
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "InstitutionalPolicy",
                "type_display_name": "InstitutionalPolicy实体节点",
                "entity_names": [
                    "废除丞相制度"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "StateOfAffairs",
                "type_display_name": "StateOfAffairs实体节点",
                "entity_names": [
                    "中央决策高度集中于皇帝"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Role",
                "type_display_name": "Role实体节点",
                "entity_names": [
                    "协调中枢",
                    "节度使"
                ],
                "total_count": 2
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Institution",
                "type_display_name": "Institution实体节点",
                "entity_names": [
                    "科举"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Position",
                "type_display_name": "职位实体节点",
                "entity_names": [
                    "丞相",
                    "总理",
                    "丞相",
                    "总理"
                ],
                "total_count": 4
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Function",
                "type_display_name": "Function实体节点",
                "entity_names": [
                    "统筹大事小情"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "AdministrativeStructure",
                "type_display_name": "AdministrativeStructure实体节点",
                "entity_names": [
                    "六部直接对皇帝负责",
                    "六部直接对皇帝负责"
                ],
                "total_count": 2
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "AdministrativeProblem",
                "type_display_name": "AdministrativeProblem实体节点",
                "entity_names": [
                    "皇帝一人批不完政务"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Behavior",
                "type_display_name": "Behavior实体节点",
                "entity_names": [
                    "互相推诿责任"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Resource",
                "type_display_name": "Resource实体节点",
                "entity_names": [
                    "银子"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Situation",
                "type_display_name": "Situation实体节点",
                "entity_names": [
                    "没人拍板"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "HistoricalState",
                "type_display_name": "历史国家实体节点",
                "entity_names": [
                    "秦国"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "PoliticalCharacteristic",
                "type_display_name": "PoliticalCharacteristic实体节点",
                "entity_names": [
                    "旧贵族势力弱",
                    "中央集权程度高"
                ],
                "total_count": 2
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Location",
                "type_display_name": "地点实体节点",
                "entity_names": [
                    "关中"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Attribute",
                "type_display_name": "Attribute实体节点",
                "entity_names": [
                    "资源丰富",
                    "易守难攻",
                    "政策连续性强"
                ],
                "total_count": 3
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "HistoricalEvent",
                "type_display_name": "历史事件实体节点",
                "entity_names": [
                    "安史之乱"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "PoliticalAction",
                "type_display_name": "PoliticalAction实体节点",
                "entity_names": [
                    "中央整顿"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "PoliticalPhenomenon",
                "type_display_name": "PoliticalPhenomenon实体节点",
                "entity_names": [
                    "藩镇割据加剧"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "Right",
                "type_display_name": "Right实体节点",
                "entity_names": [
                    "募兵权",
                    "财政调度权",
                    "军事指挥权"
                ],
                "total_count": 3
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "EconomicEntity",
                "type_display_name": "EconomicEntity实体节点",
                "entity_names": [
                    "中央财政"
                ],
                "total_count": 1
            },
            {
                "result_type": "entity_nodes_creation",
                "entity_type": "System",
                "type_display_name": "系统实体节点",
                "entity_names": [
                    "募兵制"
                ],
                "total_count": 1
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 1,
                "source_entity": "记忆熊",
                "relation_type": "MENTIONS",
                "target_entity": "宋朝军事弱势",
                "relationship_text": "记忆熊 -[MENTIONS]-> 宋朝军事弱势"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 2,
                "source_entity": "宋朝军事弱势",
                "relation_type": "RESULTED_IN",
                "target_entity": "制度性因素",
                "relationship_text": "宋朝军事弱势 -[RESULTED_IN]-> 制度性因素"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 3,
                "source_entity": "记忆熊",
                "relation_type": "MENTIONS",
                "target_entity": "禁军由文官控制导致作战效率低下",
                "relationship_text": "记忆熊 -[MENTIONS]-> 禁军由文官控制导致作战效率低下"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 4,
                "source_entity": "禁军由文官控制",
                "relation_type": "RESULTED_IN",
                "target_entity": "作战效率低下",
                "relationship_text": "禁军由文官控制 -[RESULTED_IN]-> 作战效率低下"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 5,
                "source_entity": "记忆熊",
                "relation_type": "MENTIONS",
                "target_entity": "厢军装备不足",
                "relationship_text": "记忆熊 -[MENTIONS]-> 厢军装备不足"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 6,
                "source_entity": "记忆熊",
                "relation_type": "MENTIONS",
                "target_entity": "宋朝",
                "relationship_text": "记忆熊 -[MENTIONS]-> 宋朝"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 7,
                "source_entity": "记忆熊",
                "relation_type": "MENTIONS",
                "target_entity": "军费",
                "relationship_text": "记忆熊 -[MENTIONS]-> 军费"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 8,
                "source_entity": "军费",
                "relation_type": "HAS_A",
                "target_entity": "财政支出",
                "relationship_text": "军费 -[HAS_A]-> 财政支出"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 9,
                "source_entity": "宋朝",
                "relation_type": "HAS_REVENUE",
                "target_entity": "财政支出",
                "relationship_text": "宋朝 -[HAS_REVENUE]-> 财政支出"
            },
            {
                "result_type": "relationship_creation",
                "relationship_index": 10,
                "source_entity": "持续战争",
                "relation_type": "RESULTED_IN",
                "target_entity": "财政压力",
                "relationship_text": "持续战争 -[RESULTED_IN]-> 财政压力"
            }
        ],
        "status": "completed",
        "result": {
            "dialogue_nodes_count": 1,
            "chunk_nodes_count": 2,
            "statement_nodes_count": 38,
            "entity_nodes_count": 148,
            "statement_chunk_edges_count": 38,
            "statement_entity_edges_count": 148,
            "entity_entity_edges_count": 88
        }
    },
    {
        "data": [
            {
                "result_type": "entity_merge",
                "merged_entity_name": "记忆熊",
                "merged_count": 9,
                "message": "记忆熊合并9个：相似实体已合并"
            },
            {
                "result_type": "entity_merge",
                "merged_entity_name": "宋朝",
                "merged_count": 4,
                "message": "宋朝合并4个：相似实体已合并"
            },
            {
                "result_type": "entity_merge",
                "merged_entity_name": "军费",
                "merged_count": 2,
                "message": "军费合并2个：相似实体已合并"
            },
            {
                "result_type": "entity_merge",
                "merged_entity_name": "财政支出",
                "merged_count": 2,
                "message": "财政支出合并2个：相似实体已合并"
            },
            {
                "result_type": "entity_merge",
                "merged_entity_name": "财政压力",
                "merged_count": 3,
                "message": "财政压力合并3个：相似实体已合并"
            },
            {
                "result_type": "entity_disambiguation",
                "disambiguated_entity_name": "节度使",
                "disambiguation_type": "消歧阻断：Role vs Person",
                "confidence": "0.95",
                "reason": "实体A类型为'Role'，实体B类型为'Person'，二者类型完全不同且无别名交集或名称-别名匹配。'节度使'是官职而非具体人物，语义上不应合并。尽管名称文本相似度高，但类型相似度为0.0，且上下文...",
                "message": "节度使消歧完成：消歧阻断：Role vs Person"
            }
        ],
        "status": "completed",
        "result": {
            "entities": {
                "original_count": 148,
                "final_count": 110,
                "reduced_count": 38,
                "reduction_rate": 25.7
            },
            "statement_entity_edges": {
                "original_count": 148,
                "final_count": 148,
                "reduced_count": 0
            },
            "entity_entity_edges": {
                "original_count": 88,
                "final_count": 81,
                "reduced_count": 7
            },
            "dedup_examples": [
                {
                    "type": "精确匹配",
                    "entity_name": "记忆熊",
                    "entity_type": "Person",
                    "merge_count": 8,
                    "description": "记忆熊实体去重合并8个"
                },
                {
                    "type": "精确匹配",
                    "entity_name": "宋朝",
                    "entity_type": "Organization",
                    "merge_count": 2,
                    "description": "宋朝实体去重合并2个"
                },
                {
                    "type": "精确匹配",
                    "entity_name": "军费",
                    "entity_type": "EconomicMetric",
                    "merge_count": 1,
                    "description": "军费实体去重合并1个"
                },
                {
                    "type": "精确匹配",
                    "entity_name": "学生",
                    "entity_type": "Person",
                    "merge_count": 5,
                    "description": "学生实体去重合并5个"
                },
                {
                    "type": "精确匹配",
                    "entity_name": "废除丞相制度",
                    "entity_type": "Event",
                    "merge_count": 3,
                    "description": "废除丞相制度实体去重合并3个"
                }
            ],
            "disamb_examples": [
                {
                    "entity1_name": "节度使",
                    "entity1_type": "Role",
                    "entity2_name": "节度使",
                    "entity2_type": "Person",
                    "description": "节度使和节度使，消歧区分成功"
                }
            ],
            "summary": {
                "total_merges": 39,
                "total_disambiguations": 1
            }
        }
    }
]
// 按type聚合数据的处理函数
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

export const mockTestResult = {
    "generated_at": "2025-12-12T09:48:43.389893",
    "entities": {
      "extracted_count": 148
    },
    "dedup": {
      "total_merged_count": 39,
      "breakdown": {
        "exact": 30,
        "fuzzy": 0,
        "llm": 9
      },
      "impact": [
        {
          "name": "记忆熊",
          "type": "Person",
          "appear_count": 9,
          "merge_count": 8
        },
        {
          "name": "宋朝",
          "type": "Organization",
          "appear_count": 5,
          "merge_count": 2
        },
        {
          "name": "军费",
          "type": "EconomicMetric",
          "appear_count": 2,
          "merge_count": 1
        },
        {
          "name": "学生",
          "type": "Person",
          "appear_count": 6,
          "merge_count": 5
        },
        {
          "name": "废除丞相制度",
          "type": "Event",
          "appear_count": 6,
          "merge_count": 3
        },
        {
          "name": "六部",
          "type": "Organization",
          "appear_count": 4,
          "merge_count": 3
        },
        {
          "name": "六部缺乏协调机制",
          "type": "Concept",
          "appear_count": 2,
          "merge_count": 1
        },
        {
          "name": "丞相",
          "type": "Position",
          "appear_count": 4,
          "merge_count": 1
        },
        {
          "name": "总理",
          "type": "Position",
          "appear_count": 2,
          "merge_count": 1
        },
        {
          "name": "各部委",
          "type": "Organization",
          "appear_count": 2,
          "merge_count": 1
        },
        {
          "name": "六部直接对皇帝负责",
          "type": "AdministrativeStructure",
          "appear_count": 2,
          "merge_count": 1
        },
        {
          "name": "秦国",
          "type": "Organization",
          "appear_count": 5,
          "merge_count": 2
        },
        {
          "name": "文官集团",
          "type": "Organization",
          "appear_count": 2,
          "merge_count": 1
        }
      ]
    },
    "disambiguation": {
      "block_count": 1,
      "effects": [
        {
          "left": {
            "name": "节度使",
            "type": "Role"
          },
          "right": {
            "name": "节度使",
            "type": "Person"
          },
          "result": "成功区分"
        }
      ]
    },
    "memory": {
      "chunks": 2
    },
    "triplets": {
      "count": 88
    },
    "core_entities": [
      {
        "type": "Organization",
        "type_cn": "组织",
        "count": 16,
        "entities": [
          "厂卫机构",
          "西厂",
          "东厂",
          "工部",
          "地方军阀"
        ]
      },
      {
        "type": "Event",
        "type_cn": "事件",
        "count": 12,
        "entities": [
          "均田制瓦解",
          "无法批阅完所有政务",
          "废除丞相制度",
          "持续战争",
          "政令执行困难"
        ]
      },
      {
        "type": "Condition",
        "type_cn": "Condition",
        "count": 9,
        "entities": [
          "缺乏协作机制",
          "作战效率低下",
          "厢军装备不足",
          "军权分散",
          "军事专业化难以提升"
        ]
      },
      {
        "type": "Person",
        "type_cn": "人物",
        "count": 8,
        "entities": [
          "官员",
          "宦官",
          "节度使",
          "皇帝",
          "文士"
        ]
      },
      {
        "type": "Concept",
        "type_cn": "Concept",
        "count": 8,
        "entities": [
          "行政紧张",
          "军力不足",
          "秦国统一六国的原因",
          "六部缺乏协调机制",
          "专业分工"
        ]
      },
      {
        "type": "Action",
        "type_cn": "Action",
        "count": 6,
        "entities": [
          "再花钱募兵",
          "建立军功爵制度",
          "裁撤兵员",
          "削减装备",
          "建立法律制度"
        ]
      },
      {
        "type": "Outcome",
        "type_cn": "Outcome",
        "count": 5,
        "entities": [
          "打仗更吃亏",
          "提升国家组织能力",
          "降低行政效率",
          "士兵效忠个人而非国家",
          "政令推行困难"
        ]
      },
      {
        "type": "EconomicMetric",
        "type_cn": "EconomicMetric",
        "count": 4,
        "entities": [
          "财政",
          "财政支出",
          "支出",
          "军费"
        ]
      },
      {
        "type": "Statement",
        "type_cn": "Statement",
        "count": 3,
        "entities": [
          "没有银子",
          "禁军由文官控制导致作战效率低下",
          "武器没材料"
        ]
      },
      {
        "type": "State",
        "type_cn": "State",
        "count": 3,
        "entities": [
          "军队更弱",
          "理解不足",
          "不足"
        ]
      },
      {
        "type": "HistoricalPeriod",
        "type_cn": "HistoricalPeriod",
        "count": 3,
        "entities": [
          "春秋战国史",
          "唐朝史",
          "宋朝"
        ]
      },
      {
        "type": "Attribute",
        "type_cn": "Attribute",
        "count": 3,
        "entities": [
          "资源丰富",
          "易守难攻",
          "政策连续性强"
        ]
      },
      {
        "type": "Right",
        "type_cn": "Right",
        "count": 3,
        "entities": [
          "军事指挥权",
          "财政调度权",
          "募兵权"
        ]
      },
      {
        "type": "Policy",
        "type_cn": "Policy",
        "count": 2,
        "entities": [
          "商鞅变法",
          "禁军由文官控制"
        ]
      },
      {
        "type": "MilitaryCondition",
        "type_cn": "MilitaryCondition",
        "count": 2,
        "entities": [
          "军力不足",
          "缺乏战略纵深"
        ]
      },
      {
        "type": "Role",
        "type_cn": "Role",
        "count": 2,
        "entities": [
          "节度使",
          "协调中枢"
        ]
      },
      {
        "type": "Position",
        "type_cn": "Position",
        "count": 2,
        "entities": [
          "总理",
          "丞相"
        ]
      },
      {
        "type": "PoliticalCharacteristic",
        "type_cn": "PoliticalCharacteristic",
        "count": 2,
        "entities": [
          "旧贵族势力弱",
          "中央集权程度高"
        ]
      },
      {
        "type": "Phenomenon",
        "type_cn": "Phenomenon",
        "count": 1,
        "entities": [
          "宋朝军事弱势"
        ]
      },
      {
        "type": "Factor",
        "type_cn": "Factor",
        "count": 1,
        "entities": [
          "制度性因素"
        ]
      },
      {
        "type": "EconomicFactor",
        "type_cn": "EconomicFactor",
        "count": 1,
        "entities": [
          "财政压力"
        ]
      },
      {
        "type": "EconomicIndicator",
        "type_cn": "EconomicIndicator",
        "count": 1,
        "entities": [
          "财政支出"
        ]
      },
      {
        "type": "MilitaryStrategy",
        "type_cn": "MilitaryStrategy",
        "count": 1,
        "entities": [
          "对外战略被动"
        ]
      },
      {
        "type": "MilitaryCapability",
        "type_cn": "MilitaryCapability",
        "count": 1,
        "entities": [
          "机动能力弱"
        ]
      },
      {
        "type": "PersonGroup",
        "type_cn": "PersonGroup",
        "count": 1,
        "entities": [
          "武将"
        ]
      },
      {
        "type": "EconomicCondition",
        "type_cn": "EconomicCondition",
        "count": 1,
        "entities": [
          "财政压力"
        ]
      },
      {
        "type": "InstitutionalPolicy",
        "type_cn": "InstitutionalPolicy",
        "count": 1,
        "entities": [
          "废除丞相制度"
        ]
      },
      {
        "type": "StateOfAffairs",
        "type_cn": "StateOfAffairs",
        "count": 1,
        "entities": [
          "中央决策高度集中于皇帝"
        ]
      },
      {
        "type": "Institution",
        "type_cn": "Institution",
        "count": 1,
        "entities": [
          "科举"
        ]
      },
      {
        "type": "Function",
        "type_cn": "Function",
        "count": 1,
        "entities": [
          "统筹大事小情"
        ]
      },
      {
        "type": "AdministrativeStructure",
        "type_cn": "AdministrativeStructure",
        "count": 1,
        "entities": [
          "六部直接对皇帝负责"
        ]
      },
      {
        "type": "AdministrativeProblem",
        "type_cn": "AdministrativeProblem",
        "count": 1,
        "entities": [
          "皇帝一人批不完政务"
        ]
      },
      {
        "type": "Behavior",
        "type_cn": "Behavior",
        "count": 1,
        "entities": [
          "互相推诿责任"
        ]
      },
      {
        "type": "Resource",
        "type_cn": "Resource",
        "count": 1,
        "entities": [
          "银子"
        ]
      },
      {
        "type": "Situation",
        "type_cn": "Situation",
        "count": 1,
        "entities": [
          "没人拍板"
        ]
      },
      {
        "type": "HistoricalState",
        "type_cn": "HistoricalState",
        "count": 1,
        "entities": [
          "秦国"
        ]
      },
      {
        "type": "Location",
        "type_cn": "地点",
        "count": 1,
        "entities": [
          "关中"
        ]
      },
      {
        "type": "HistoricalEvent",
        "type_cn": "HistoricalEvent",
        "count": 1,
        "entities": [
          "安史之乱"
        ]
      },
      {
        "type": "PoliticalAction",
        "type_cn": "PoliticalAction",
        "count": 1,
        "entities": [
          "中央整顿"
        ]
      },
      {
        "type": "PoliticalPhenomenon",
        "type_cn": "PoliticalPhenomenon",
        "count": 1,
        "entities": [
          "藩镇割据加剧"
        ]
      },
      {
        "type": "EconomicEntity",
        "type_cn": "EconomicEntity",
        "count": 1,
        "entities": [
          "中央财政"
        ]
      },
      {
        "type": "System",
        "type_cn": "System",
        "count": 1,
        "entities": [
          "募兵制"
        ]
      },
      {
        "type": "WorkRole",
        "type_cn": "WorkRole",
        "count": 1,
        "entities": [
          "掌控禁军"
        ]
      }
    ],
    "triplet_samples": [
      {
        "subject": "记忆熊",
        "predicate": "MENTIONS",
        "predicate_cn": "提到",
        "object": "宋朝军事弱势"
      },
      {
        "subject": "宋朝军事弱势",
        "predicate": "RESULTED_IN",
        "predicate_cn": "resulted in",
        "object": "制度性因素"
      },
      {
        "subject": "记忆熊",
        "predicate": "MENTIONS",
        "predicate_cn": "提到",
        "object": "禁军由文官控制导致作战效率低下"
      },
      {
        "subject": "禁军由文官控制",
        "predicate": "RESULTED_IN",
        "predicate_cn": "resulted in",
        "object": "作战效率低下"
      },
      {
        "subject": "记忆熊",
        "predicate": "MENTIONS",
        "predicate_cn": "提到",
        "object": "厢军装备不足"
      },
      {
        "subject": "记忆熊",
        "predicate": "MENTIONS",
        "predicate_cn": "提到",
        "object": "宋朝"
      },
      {
        "subject": "记忆熊",
        "predicate": "MENTIONS",
        "predicate_cn": "提到",
        "object": "军费"
      }
    ],
    "self_reflexion": [
      {
        "conflict": {
          "data": [
            {
              "id": "76be6d82d8804beda6baa3d3447d6cbc",
              "statement": "学生对\"六部缺乏协调机制\"的具体影响表示理解不足。",
              "group_id": "group_123",
              "chunk_id": "4a0804127d35456f86d4f06e1fa458f7",
              "created_at": "2025-12-12 09:48:00.166068",
              "expired_at": null,
              "valid_at": null,
              "invalid_at": null,
              "entity_ids": []
            }
          ],
          "conflict": true,
          "conflict_memory": {
            "id": "e268a6fff35543fab471986c188e023e",
            "statement": "学生对\"六部缺乏协调机制\"的具体影响表示理解不足。",
            "group_id": "group_123",
            "chunk_id": "e6cb5f56020e4a8d925d148e1d2fbda0",
            "created_at": "2025-12-12 09:48:00.166068",
            "expired_at": null,
            "valid_at": null,
            "invalid_at": null,
            "entity_ids": []
          }
        },
        "reflexion": {
          "reason": "同一学生在不同时间点重复提出对'六部缺乏协调机制'具体影响的理解困难，表明原有解释未能有效解决其认知障碍，存在记忆冗余与教学反馈失效的冲突。",
          "solution": "保留后出现的记忆记录（chunk_id为4a0804127d35456f86d4f06e1fa458f7）作为最新学习状态，将其设为有效；将前次相同内容的记忆（id为e268a6fff35543fab471986c188e023e）标记为失效，避免重复干预，并基于后续完整解释优化知识呈现逻辑。"
        },
        "resolved": {
          "original_memory_id": "e268a6fff35543fab471986c188e023e",
          "resolved_memory": {
            "id": "e268a6fff35543fab471986c188e023e",
            "statement": "学生对\"六部缺乏协调机制\"的具体影响表示理解不足。",
            "group_id": "group_123",
            "chunk_id": "e6cb5f56020e4a8d925d148e1d2fbda0",
            "created_at": "2025-12-12 09:48:00.166068",
            "expired_at": null,
            "valid_at": null,
            "invalid_at": "2025-12-12 09:48:00.166068",
            "entity_ids": []
          }
        }
      }
    ]
  }