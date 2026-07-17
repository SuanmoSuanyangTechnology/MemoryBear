/*
 * Result module type definitions
 */

/**
 * Result component props
 */
export interface ResultProps {
  loading: boolean;
  handleSave: () => void;
  /** Disable the save action when using the system default config */
  disabled?: boolean;
  /** Whether semantic pruning is enabled; hides the pruning module when false */
  pruningEnabled?: boolean;
}

/**
 * Module processing item
 */
export interface ModuleItem {
  status: 'pending' | 'processing' | 'completed' | 'failed';
  data: any[];
  result: any;
  start_at?: number;
  end_at?: number;
}
