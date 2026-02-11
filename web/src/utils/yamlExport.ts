/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:35:32 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:35:32 
 */
/**
 * YAML Export Utility
 * 
 * Provides functions to export data as YAML files.
 * 
 * @module yamlExport
 */

import yaml from 'js-yaml';

/**
 * Export data to YAML file
 * @param data - Data to export
 * @param filename - Output filename (default: 'export.yaml')
 */
export const exportToYaml = (data: unknown, filename: string = 'export.yaml') => {
  const yamlStr = yaml.dump(data);
  const blob = new Blob([yamlStr], { type: 'text/yaml' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
};
