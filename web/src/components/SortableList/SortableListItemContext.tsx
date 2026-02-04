/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 15:27:52 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 15:27:52 
 */
/**
 * SortableListItemContext
 * 
 * React context for sharing sortable item properties with child components.
 * Used by DragHandle to access drag-and-drop functionality.
 * 
 * @context
 */

import { createContext } from 'react';

import type { SortableListItemContextProps } from './types';

/** Context for sharing sortable item properties with child components (e.g., DragHandle) */
const SortableListItemContext = createContext<SortableListItemContextProps>({});

export default SortableListItemContext;