import yaml from 'js-yaml';


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
