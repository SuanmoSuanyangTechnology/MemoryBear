import { createContext } from 'react';
import type { SortableListItemContextProps } from './types';


const SortableListItemContext = createContext<SortableListItemContextProps>({});

export default SortableListItemContext;