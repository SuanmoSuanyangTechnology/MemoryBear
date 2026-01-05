export interface Data {
  end_user: {
    id: string;
    app_id: string;
    other_id: string;
    other_name: string;
    other_address: string;
    created_at: string;
    updated_at: string;
  },
  memory_num: {
    total: number;
    counts: {
      dialogue: number;
      chunk: number;
      statement: number;
      entity: number;
    }
  },
  name?: string;
}
export interface ConfigModalData {
  llm: string;
  embedding: string;
  rerank: string;
}
export interface ConfigModalRef {
  handleOpen: () => void;
}