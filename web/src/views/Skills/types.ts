import type { UploadFile } from 'antd';

// List query parameters
export interface SkillQuery {
  /**
   * Active filter; the list page always passes true
   */
  is_active?: boolean;
  /**
   * Public filter
   */
  is_public?: boolean;
  /**
   * Granularity: all | plugin | skill (default: all)
   */
  kind?: string;
  /**
   * Page number, >= 1 (default: 1)
   */
  page?: number;
  /**
   * Items per page, 1-100 (default: 10)
   */
  pagesize?: number;
  /**
   * Search keywords: name / description / call_name / id
   */
  search?: string;
  /**
   * Filter by exact card IDs; multiple IDs can be comma-separated
   * Skill row ID / package_id / plugin ID; matching any one returns the result (union), intersected with other filters.
   * Empty segments are ignored; non-UUID values return 1000/400
   */
  ids?: string;
}

/**
 * Type
 */
export enum Kind {
  Plugin = "plugin",
  Skill = "skill",
}
export interface Member {
  /**
   * Runtime call name
   */
  call_name?: null | string;
  /**
   * Member description
   */
  description?: null | string;
  /**
   * Whether the member package contains scripts/
   */
  has_scripts?: boolean;
  /**
   * Member skill row ID
   */
  id?: string;
  /**
   * Whether the member row is active
   */
  is_active?: boolean;
  /**
   * Member display name
   */
  name?: string;
  /**
   * Member package ID
   */
  package_id?: string;
}
/**
 * Dual format support
 */
export enum SkillFormat {
    Legacy = "legacy",
    Standard = "standard",
}


// List
export interface LegacySkill {
  /**
   * Display name
   */
  name: string;
  /**
   * Description
   */
  description: string;
  /** Array of tools associated with this skill */
  tools: Array<{
    /** Tool identifier */
    tool_id: string;
    /** Tool operation/action */
    operation?: string;
  }>;
  /** Skill configuration settings */
  config: {
    /** Keywords for skill matching and discovery */
    keywords: string[];
    /** Whether the skill is enabled */
    enabled: boolean;
  };
  /** AI prompt/instructions for the skill */
  prompt: string;
  /** Whether the skill is active */
  is_active: boolean;
  /** Whether the skill is publicly accessible */
  is_public: boolean;
}
export interface Skill extends LegacySkill {
  /**
   * Runtime call name
   */
  call_name?: null | string;
  /**
   * Creation time (millisecond timestamp)
   */
  created_at?: number;
  /**
   * Whether scripts/ is included
   */
  has_scripts?: boolean | null;
  /**
   * Skill row ID; for plugin cards, this is the plugin ID
   */
  id: string;
  /**
   * Granularity
   */
  kind?: Kind;
  /**
   * Every card has non-empty members: plugin cards contain active member summaries; standalone skills (kind=skill) contain a single-element array representing themselves (same shape as plugin members). files_count is not included; request the detail endpoint for file counts.
   */
  members?: Member[] | null;
  /**
   * Package ID referenced by a standard skill
   */
  package_id?: null | string;
  /**
   * Card: the plugin card uses its own ID; detail: a standard skill uses its parent plugin ID, while legacy or unpackaged skills use null
   */
  plugin_id?: null | string;
  /**
   * Dual format support
   */
  skill_format?: SkillFormat;
  /**
   * Source (URL / zip filename)
   */
  source?: null | string;
  /**
   * Tenant ownership
   */
  tenant_id?: string;
  /**
   * Update time (millisecond timestamp)
   */
  updated_at?: number;
}


// Details
export interface FileDetail {
  /**
   * Text content; null for binary files
   */
  content?: null | string;
  /**
   * Whether the file is binary
   */
  is_binary?: boolean;
  /**
   * Relative path within the package
   */
  rel_path?: string;
  /**
   * Byte count
   */
  size?: number;
}
export interface MemberDetail extends Member {
  /**
   * Member package file count (returned only in details)
   */
  files_count?: number;
}
export interface SkillDetail extends Omit<Skill, 'members'> {
  /**
   * Plugin member summaries (the details endpoint includes all members and files_count)
   */
  members?: MemberDetail[] | null;
  /**
   * Details only: files within the package
   */
  files?: FileDetail[] | null;
  /**
   * Details only: full SKILL.md content
   */
  skill_md?: null | string;
}

// Import parameters
export interface SkillPackageImportFormData {
  // Standard package zip (containing SKILL.md); mutually exclusive with url
  file?: UploadFile
  // GitHub repository/directory URL; mutually exclusive with file
  url?: string
  // true = parse and preview only; do not persist or consume quota (default: false)
  dry_run?: string
  // JSON array: member identifiers to retain for plugins; defaults to retaining all
  members?: string
  // JSON: member-level metadata overrides [{identifier,name,description,call_name}]
  meta?: string
  // Plugin display name when kind=plugin
  plugin_name?: string
  // Plugin description
  plugin_description?: string
  // Whether to overwrite differing content with the same name (default: false)
  overwrite?: string
  // Conflict with same-name legacy content: rename | overwrite (default: rename)
  conflict_strategy?: 'rename' | 'overwrite'
}
// Import response
export interface ImportMember {
    call_name?: null | string;
    conflict_resolved?: null | string;
    description?: null | string;
    /**
     * Preview only: array of relative paths
     */
    files?: string[] | null;
    /**
     * Persisted response only: integer
     */
    files_count?: number | null;
    /**
     * Preview only: array of file objects with content (same shape as detail files[]); binary content is null and is not returned by the list endpoint
     */
    files_detail?: FileDetail[] | null;
    has_scripts?: boolean;
    idempotent?: boolean;
    /**
     * Member key: return exactly what the preview provides
     */
    identifier?: string;
    name?: string;
    package_id?: null | string;
    skill_id?: null | string;
}
export interface ImportSkill {
  call_name?: null | string;
  /**
   * SHA-256 of the entire package
   */
  content_hash?: null | string;
  description?: null | string;
  dry_run?: boolean;
  files_count?: number | null;
  idempotent?: boolean;
  kind?: Kind;
  members?: ImportMember[];
  name?: string;
  /**
   * Non-empty when kind=skill is persisted
   */
  package_id?: null | string;
  plugin_id?: null | string;
  /**
   * Non-empty when kind=skill is persisted
   */
  skill_id?: null | string;
  /**
   * Import source (URL / filename)
   */
  source?: null | string;
  warnings?: string[];
}