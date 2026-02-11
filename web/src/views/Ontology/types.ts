/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:10 
 */
/**
 * Query parameters for ontology list pagination and filtering
 */
export interface Query {
  /** Number of items per page */
  pagesize?: number;
  /** Current page number */
  page?: number;
  /** Scene name for filtering */
  scene_name?: string;
}

/**
 * Ontology scene item data structure
 */
export interface OntologyItem {
  /** Unique identifier for the scene */
  scene_id: string;
  /** Name of the ontology scene */
  scene_name: string;
  /** Description of the ontology scene */
  scene_description: string;
  /** Number of entity types in the scene */
  type_num: number;
  /** Array of entity type names */
  entity_type: string[];
  /** Associated workspace identifier */
  workspace_id: string;
  /** Creation timestamp */
  created_at: number;
  /** Last update timestamp */
  updated_at: number;
  /** Total count of classes in the scene */
  classes_count: number;
}

/**
 * Form data for creating/editing ontology scene
 */
export interface OntologyModalData {
  /** Scene name */
  scene_name: string;
  /** Scene description */
  scene_description: string;
}

/**
 * Ref methods exposed by OntologyModal component
 */
export interface OntologyModalRef {
  /**
   * Open the modal for creating or editing
   * @param data - Optional ontology item data for editing mode
   */
  handleOpen: (data?: OntologyItem) => void;
}

/**
 * Ontology class item data structure
 */
export interface OntologyClassItem {
  /** Unique identifier for the class */
  class_id: string;
  /** Name of the class */
  class_name: string;
  /** Description of the class */
  class_description: string;
  /** Associated scene identifier */
  scene_id: string;
  /** Creation timestamp */
  created_at: number;
  /** Last update timestamp */
  updated_at: number;
}
/**
 * Response data structure for ontology class list
 */
export interface OntologyClassData {
  /** Total number of classes */
  total: number;
  /** Scene identifier */
  scene_id: string;
  /** Scene name */
  scene_name: string;
  /** Scene description */
  scene_description: string;
  /** Array of class items */
  items: OntologyClassItem[];
}

/**
 * Data structure for adding a new class
 */
export interface AddClassItem {
  /** Name of the class to add */
  class_name: string;
  /** Description of the class to add */
  class_description: string;
}
/**
 * Form data for creating ontology classes
 */
export interface OntologyClassModalData {
  /** Target scene identifier */
  scene_id: string;
  /** Array of classes to create */
  classes: AddClassItem[]
}
/**
 * Ref methods exposed by OntologyClassModal component
 */
export interface OntologyClassModalRef {
  /**
   * Open the modal for adding classes
   * @param scene_id - Target scene identifier
   */
  handleOpen: (scene_id: string) => void;
}
/**
 * Form data for extracting ontology classes using LLM
 */
export interface OntologyClassExtractModalData {
  /** LLM model identifier */
  llm_id: string;
  /** Target scene identifier */
  scene_id: string;
  /** Scenario description for extraction */
  scenario: string;
  /** Domain name (same as scene_name) */
  domain: string;
}
/**
 * Ref methods exposed by OntologyClassExtractModal component
 */
export interface OntologyClassExtractModalRef {
  /**
   * Open the modal for extracting classes
   * @param vo - Ontology class data containing scene information
   */
  handleOpen: (vo: OntologyClassData) => void;
}

/**
 * Extracted class item from LLM
 */
export interface ExtractClassItem {
  /** Unique identifier for the extracted class */
  id: string;
  /** English name of the class */
  name: string;
  /** Chinese name of the class */
  name_chinese: string;
  /** Description of the class */
  description: string;
  /** Example instances of the class */
  examples: string[];
  /** Parent class name if exists */
  parent_class: string | null;
  /** Entity type classification */
  entity_type: string;
  /** Domain the class belongs to */
  domain: string;
}
/**
 * Response data structure for class extraction
 */
export interface ExtractData {
  /** Domain name */
  domain: string;
  /** Number of classes extracted */
  extracted_count: number;
  /** Array of extracted class items */
  classes: ExtractClassItem[]
}
/**
 * Ref methods exposed by OntologyImportModal component
 */
export interface OntologyImportModalRef {
  /** Open the import modal */
  handleOpen: () => void;
}
/**
 * Form data for importing ontology
 */
export interface OntologyImportModalData {
  /** Name for the imported scene */
  scene_name: string;
  /** Optional description for the imported scene */
  scene_description?: string;
  /** File to import (OWL, TTL, RDF, XML formats) */
  file: any;
}
/**
 * Ref methods exposed by OntologyExportModal component
 */
export interface OntologyExportModalRef {
  /** Open the export modal */
  handleOpen: () => void;
}
/**
 * Form data for exporting ontology
 */
export interface OntologyExportModalData {
  /** Scene identifier to export */
  scene_id: string;
  /** Export format: 'rdfxml' (.owl) or 'turtle' (.ttl) */
  format: 'rdfxml' | 'turtle';
}