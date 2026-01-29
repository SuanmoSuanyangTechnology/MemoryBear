export interface Query {
  pagesize?: number;
  page?: number;
  scene_name?: string;
}

export interface OntologyItem {
  scene_id: string;
  scene_name: string;
  scene_description: string;
  type_num: number;
  entity_type: string[];
  workspace_id: string;
  created_at: number;
  updated_at: number;
  classes_count: number;
}

export interface OntologyModalData {
  scene_name: string;
  scene_description: string;
}

export interface OntologyModalRef {
  handleOpen: (data?: OntologyItem) => void;
}

export interface OntologyClassItem {
  class_id: string;
  class_name: string;
  class_description: string;
  scene_id: string;
  created_at: number;
  updated_at: number;
}
export interface OntologyClassData {
  total: number;
  scene_id: string;
  scene_name: string;
  scene_description: string;
  items: OntologyClassItem[];
}

export interface AddClassItem {
  class_name: string;
  class_description: string;
}
export interface OntologyClassModalData {
  scene_id: string;
  classes: AddClassItem[]
}
export interface OntologyClassModalRef {
  handleOpen: (scene_id: string) => void;
}
export interface OntologyClassExtractModalData {
  llm_id: string;
  scene_id: string;
  scenario: string;
  domain: string; // scene_name
}
export interface OntologyClassExtractModalRef {
  handleOpen: (vo: OntologyClassData) => void;
}

export interface ExtractClassItem {
  id: string;
  name: string;
  name_chinese: string;
  description: string;
  examples: string[];
  parent_class: string | null;
  entity_type: string;
  domain: string;
}
export interface ExtractData {
  domain: string;
  extracted_count: number;
  classes: ExtractClassItem[]
}
