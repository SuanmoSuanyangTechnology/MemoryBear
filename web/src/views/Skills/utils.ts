import type { Skill } from './types';
import { Kind } from './types';

export const isPlugin = (skill: Skill) => skill.kind === Kind.Plugin;