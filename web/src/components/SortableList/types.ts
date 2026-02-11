/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:29:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:29:39 
 */
/**
 * SortableList Type Definitions
 * 
 * Type definitions for sortable list components.
 * Defines the context props interface for drag-and-drop functionality.
 */

import type { SyntheticListenerMap } from '@dnd-kit/core/dist/hooks/utilities';
import type { DraggableAttributes } from '@dnd-kit/core';

/** Props interface for SortableListItem context */
export interface SortableListItemContextProps {
  /** Function to set the activator node ref for drag handle */
  setActivatorNodeRef?: (element: HTMLElement | null) => void;
  /** Event listeners for drag interactions */
  listeners?: SyntheticListenerMap;
  /** Accessibility attributes for draggable elements */
  attributes?: DraggableAttributes;
}