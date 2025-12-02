export interface ConfigForm {
  llm_id: string;
  config_id?: number | string;
  enable_llm_dedup_blockwise: boolean;
  enable_llm_disambiguation: boolean;
  t_type_strict: string | number;
  t_name_strict: string | number;
  t_overall: string | number;
  deep_retrieval: boolean;
  chunker_strategy: string;

  pruning_enabled: boolean;
  pruning_scene: string;
  pruning_threshold: string | number;

  enable_self_reflexion: boolean;
  iteration_period: number;
  reflexion_range: string;
  baseline: string;
  
}
export interface Variable {
  label: string;
  variableName: string;
  control: string;
  meaning?: string;
  options?: {
    label: string;
    value: string | number;
  }[];
  type: string;
  min?: number;
  max?: number;
  step?: number;
}
export interface ConfigVo {
  type: string;
  data: {
    title: string;
    list: Variable[]
  }[]
}

export interface TestResult {
  generated_at: string;
  entities: Record<string, number>;
  dedup: {
    total_merged_count: number;
    breakdown: {
      exact: number;
      fuzzy: number;
      llm: number;
    };
    impact: {
      name: string;
      type: string;
      appear_count: number;
      merge_count: number;
    }[];
  };
  disambiguation: {
    block_count: number;
    effects: {
      left: {
        name: string;
        type: string;
      };
      right: {
        name: string;
        type: string;
      };
      result: string;
    }[];
  };
  memory: {
    chunks: number;
  };
  triplets: {
    count: number;
  };
  core_entities: {
    type: string;
    count: number;
    entities: string[];
  }[];
  triplet_samples: {
    subject: string;
    predicate: string;
    object: string;
  }[];
}