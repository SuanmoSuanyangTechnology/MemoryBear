import type { NodeLibrary } from '../types';
import { nodeLibraryPart1 } from './nodeLibraryPart1';
import { nodeLibraryPart2 } from './nodeLibraryPart2';

/**
 * Workflow node library configuration
 * Defines all available node types, their icons, and configuration schemas
 */
export const nodeLibrary: NodeLibrary[] = [...nodeLibraryPart1, ...nodeLibraryPart2];
