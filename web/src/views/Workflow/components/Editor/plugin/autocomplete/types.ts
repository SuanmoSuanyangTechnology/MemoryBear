import type { NodeProperties } from '../../../../types';

// Suggestion item interface for autocomplete dropdown
export interface Suggestion {
  key: string;
  label: string;
  type: string;
  dataType: string;
  value: string;
  group?: string;
  nodeData: NodeProperties;
  isContext?: boolean; // Flag for context variable
  disabled?: boolean; // Flag for disabled state
  children?: Suggestion[]; // Sub-variables (e.g. file fields)
  parentLabel?: string; // Parent variable label (for child display)
  default?: any;
  ui_type?: string;
  options?: string[];
  required?: boolean;
}

// Position of a floating child panel relative to the viewport
export interface PanelPos {
  top: number;
  horizontal: number;
  useRight: boolean;
}
