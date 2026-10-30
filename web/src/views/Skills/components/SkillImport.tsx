import { useTranslation } from 'react-i18next';
import { useState, type FC } from 'react';
import { flushSync } from 'react-dom';
import { App, Button, Checkbox, Flex, Form, Input } from 'antd';
import type { UploadFile } from 'antd';

import RadioGroupCard from '@/components/RadioGroupCard';
import RbModal from '@/components/RbModal';
import RbAlert from '@/components/RbAlert';
import UploadFiles from '@/components/Upload/UploadFiles';
import pluginIcon from '@/assets/images/menuNew/application_purple.svg';
import skillIcon from '@/assets/images/menuNew/skills.svg';
import textIcon from '@/assets/images/application/text.svg';
import SkillFiles from './SkillFiles';
import { skillImport } from '@/api/skill'; 
import { Kind, type ImportSkill, type SkillPackageImportFormData } from '../types';

type Source = 'github' | 'package';
type PreviewSkill = { name: string; callName: string; description: string };
type PreviewFieldPath = ['plugin', 'name' | 'description'] | ['skill', keyof PreviewSkill] | ['skills', number, keyof PreviewSkill];
type ImportForm = { source: Source; files: UploadFile[]; url: string; plugin: Pick<PreviewSkill, 'name' | 'description'>; skill: PreviewSkill; skills: PreviewSkill[] };

const SkillImport: FC<{ open: boolean; onClose: () => void; onImported?: () => void }> = ({ open, onClose, onImported }) => {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [form] = Form.useForm<ImportForm>();
  const source = Form.useWatch('source', { form, preserve: true }) ?? 'github';
  const [preview, setPreview] = useState<ImportSkill | null>(null);
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState<number[]>([]);
  const skills = Form.useWatch('skills', { form, preserve: true }) ?? [];
  const [selectedSkill, setSelectedSkill] = useState<number | null>(null);
  const reset = () => {
    form.resetFields();
    setPreview(null);
    setChecked([]);
    setSelectedSkill(null);
  };
  const close = () => {
    reset();
    onClose();
  };
  const sourceParams = (): SkillPackageImportFormData => form.getFieldValue('source') === 'github'
    ? { url: (form.getFieldValue('url') ?? '').trim() }
    : { file: form.getFieldValue('files')?.[0] };
  const parse = async () => {
    if (source === 'github') {
      try {
        const address = new URL((form.getFieldValue('url') ?? '').trim());
        if (address.protocol !== 'https:' || address.hostname !== 'github.com' || address.username || address.password || address.pathname.split('/').filter(Boolean).length < 2) {
          throw new Error();
        }
      } catch {
        return;
      }
    } else if (!form.getFieldValue('files')?.[0]?.uid) {
      return;
    }
    setLoading(true);
    try {
      const result = await skillImport({ ...sourceParams(), dry_run: 'true' });
      if (result.kind !== Kind.Plugin && result.kind !== Kind.Skill) throw new Error(t('skills.invalidPackage'));
      setPreview(result);
      form.setFieldsValue({
        plugin: { name: result.name ?? '', description: result.description ?? '' },
        skill: { name: result.name ?? '', callName: result.call_name ?? '', description: result.description ?? '' },
        skills: (result.members ?? []).map(item => ({ name: item.name ?? '', callName: item.call_name ?? '', description: item.description ?? '' })),
      });
      setChecked((result.members ?? []).map((_, index) => index));
      setSelectedSkill(null);
    } catch (err) {
      console.error(t('skills.parseFailed'), err);
    } finally {
      setLoading(false);
    }
  };
  const submit = async () => {
    if (!preview) return;
    const requiredFields: PreviewFieldPath[] = preview.kind === Kind.Plugin
      ? [['plugin', 'name'], ['plugin', 'description'], ...checked.flatMap(index =>
        (['name', 'callName', 'description'] as const).map((field): PreviewFieldPath => ['skills', index, field]))]
      : [['skill', 'name'], ['skill', 'callName'], ['skill', 'description']];
    const invalidField = requiredFields.find(name => !String(form.getFieldValue(name) ?? '').trim());
    if (invalidField) {
      // Conditional preview fields must be mounted before validation and scrolling.
      flushSync(() => setSelectedSkill(invalidField[0] === 'skills' ? Number(invalidField[1]) : null));
      try {
        await form.validateFields([invalidField]);
      } catch {
        form.scrollToField(invalidField, { behavior: 'smooth', block: 'center', focus: true });
      }
      return;
    }
    const values = form.getFieldsValue(true) as ImportForm;
    if (preview.kind === Kind.Plugin && checked.length === 0) {
      return;
    }
    if (preview.kind === Kind.Plugin && checked.some(index => !preview.members?.[index]?.identifier)) {
      return;
    }
    const params: SkillPackageImportFormData = { ...sourceParams(), dry_run: 'false' };
    if (preview.kind === Kind.Plugin) {
      params.plugin_name = values?.plugin?.name;
      params.plugin_description = values?.plugin?.description;
      params.members = JSON.stringify(checked.map(index => preview.members![index].identifier));
      params.meta = JSON.stringify(checked.map(index => ({
        identifier: preview.members![index].identifier,
        name: values.skills?.[index]?.name?.trim(),
        description: values.skills?.[index]?.description?.trim(),
        call_name: values.skills?.[index]?.callName?.trim(),
      })));
    } else {
      const identifier = preview.members?.[0]?.identifier;
      if (!identifier) {
        message.error(t('skills.invalidPackage'));
        return;
      }
      params.meta = JSON.stringify([{
        identifier,
        name: values.skill.name.trim(),
        description: values.skill.description.trim(),
        call_name: values.skill.callName.trim(),
      }]);
    }
    setLoading(true);
    try {
      const result = await skillImport(params);
      if (result.dry_run === true) throw new Error(t('skills.importIncomplete'));
      message.success(result.idempotent ? t('skills.alreadyImported') : t('skills.importSuccess'));
      onImported?.();
      close();
    } catch (err) {
      console.error(t('skills.importFailed'), err);
    } finally {
      setLoading(false);
    }
  };
  const member = selectedSkill === null ? null : skills[selectedSkill];
  const previewMember = selectedSkill !== null
    ? preview?.members?.[selectedSkill]
    : preview?.kind === Kind.Skill ? preview.members?.[0] : null;
  const files = previewMember?.files_detail?.filter(item => item.rel_path).map(item => ({
    path: item.rel_path!, content: item.content, is_binary: item.is_binary,
  })) ?? previewMember?.files?.map(path => ({ path })) ?? [];
  return (
    <RbModal
      open={open}
      onCancel={loading ? undefined : close}
      closable={!loading}
      title={preview ? t('skills.informationTitle', { value0: member ? t('skills.skill') : preview.kind === Kind.Plugin ? t('skills.plugin') : t('skills.skill') }) : t('skills.importPackage')}
      width={1000}
      footer={preview
        ? [
          <Button disabled={loading} onClick={() => { setPreview(null); setSelectedSkill(null); }}>{t('skills.reimport')}</Button>,
          <Button type="primary" loading={loading} onClick={submit}>
            {t('skills.importAction')}{preview.kind === Kind.Plugin ? t('skills.plugin') : t('skills.skill')}
          </Button>
        ]
        : [
          <Button disabled={loading} onClick={close}>{t('skills.cancel')}</Button>,
          <Button type="primary" loading={loading} onClick={() => form.submit()}>{t('skills.parsePreview')}</Button>
        ]
      }
    >
      <Form name="skill-import" form={form} initialValues={{ source: 'github', files: [] }} layout="vertical" disabled={loading}
        scrollToFirstError={{ behavior: 'smooth', block: 'center', focus: true }}
        onFinish={preview ? submit : parse}>
        {!preview
          ? (
            <div className="rb:grid rb:gap-5 rb:lg:grid-cols-[minmax(0,1fr)_240px]">
              <section className="rb:min-w-0">
                <h3 className="rb:mt-0 rb:mb-2 rb:text-base rb:font-medium">{t('skills.chooseSource')}</h3>
                <p className="rb:mt-0 rb:mb-3 rb:text-sm rb:leading-6 rb:text-gray-600">
                  {t('skills.sourceDescription')}</p>
                <Form.Item id="skill-import_source" name="source" rules={[{ required: true, message: t('common.pleaseSelect') }]}>
                  <RadioGroupCard
                    block
                    allowClear={false}
                    disabled={loading}
                    options={[
                      { value: 'github', label: t('skills.githubUrl'), labelDesc: t('skills.githubDescription') },
                      { value: 'package', label: t('skills.zipFile'), labelDesc: t('skills.zipDescription') },
                    ]}
                  />
                </Form.Item>
                {source === 'github' ? (
                  <Form.Item name="url" label={t('skills.githubUrl')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]}
                    extra={t('skills.githubHint')}
                  >
                    <Input placeholder="https://github.com/owner/repository/tree/main/skills/skill-name" />
                  </Form.Item>
                ) : (
                  <Form.Item id="skill-import_files" name="files" valuePropName="fileList"
                    rules={[{ required: true, type: 'array', min: 1, message: t('common.pleaseSelect') }]}
                    className="rb:mb-0!"
                  >
                    <UploadFiles
                      disabled={loading}
                      isAutoUpload={false}
                      isCanDrag
                      fileSize={20}
                      fileType={['zip']}
                      maxCount={1}
                      draggerHeight={200}
                    />
                  </Form.Item>
                )}
              </section>
              <aside className="rb:rounded-xl rb:border rb:border-gray-400 rb:bg-blue-700 rb:p-4">
                <h3 className="rb:mt-0 rb:mb-4 rb:text-sm rb:font-medium">{t('skills.contentsTitle')}</h3>
                <div className="rb:space-y-4">
                  {[t('skills.skillFileDescription'), t('skills.scriptsDescription'), t('skills.referencesDescription'), t('skills.assetsDescription')].map(item => {
                    const [name, ...description] = item.split(/[｜|]/);
                    return (
                      <div key={name} className="rb:grid rb:grid-cols-[16px_minmax(0,1fr)] rb:gap-x-2 rb:gap-y-1 rb:text-[13px] rb:leading-5">
                        <img src={textIcon} alt="" className="rb:mt-0.5 rb:size-4" />
                        <span className="rb:font-mono">{name.trim()}</span>
                        <p className="rb:col-start-2 rb:m-0 rb:text-gray-500">{description.join('|').trim()}</p>
                      </div>
                    );
                  })}
                </div>
                <p className="rb:mt-4 rb:mb-0 rb:text-xs rb:leading-5 rb:text-gray-600">{t('skills.requiredFileHint')}</p>
                <div className="rb:mt-5 rb:border-t rb:border-gray-400 rb:pt-4">
                  <h3 className="rb:mt-0 rb:mb-2 rb:text-sm rb:font-medium">{t('skills.pluginAndSkill')}</h3>
                  <p className="rb:mb-0 rb:text-xs rb:leading-5 rb:text-gray-600">
                    {t('skills.pluginExplanation')}</p>
                </div>
              </aside>
            </div>
          )
          : (
            <div className="rb:space-y-5">
              {!!preview.warnings?.length && (
                <RbAlert color="orange" className="rb:mb-3!">
                  <ul className="rb:min-w-0 rb:whitespace-pre-wrap rb:wrap-break-word rb:pl-6 rb:list-disc">
                    {preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
                  </ul>
                </RbAlert>
              )}
              {preview.kind === Kind.Plugin && !member ? (
                <>
                  <Flex gap={16} align="flex-end">
                    <Flex component="span" align="center" justify="center" className="rb:size-10 rb:shrink-0 rb:rounded-lg rb:bg-purple-50" aria-hidden="true">
                      <img src={pluginIcon} alt="" className="rb:h-6 rb:w-6" />
                    </Flex>
                    <Form.Item name={["plugin", "name"]} label={t('skills.pluginName')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]} className="rb:min-w-0 rb:flex-1">
                      <Input />
                    </Form.Item>
                  </Flex>
                  <div className="rb:ml-14">
                    <Form.Item name={["plugin", "description"]} label={t('skills.pluginDescription')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]}>
                      <Input.TextArea rows={3} />
                    </Form.Item>
                  </div>
                  <section aria-label={t('skills.includedSkills')}>
                    <Flex justify="space-between" align="center">
                      <h3>{t('skills.containsPrefix')}{skills.length} {t('skills.skillsSuffix')}</h3>
                      <span className="rb:text-gray-500">{t('skills.selectedPrefix')}{checked.length} {t('skills.selectedSuffix')}</span>
                    </Flex>
                    <div className="rb:grid rb:gap-3 rb:md:grid-cols-2">
                      {skills.map((item, index) => (
                        <Flex key={index} gap={12} className="rb:rounded-xl rb-border rb:bg-white rb:p-4!">
                          <Checkbox
                            aria-label={t('skills.selectSkill', { value0: item.name })}
                            checked={checked.includes(index)}
                            onChange={event => setChecked(current => event.target.checked ? [...current, index] : current.filter(value => value !== index))}
                          />
                          <div onClick={() => setSelectedSkill(index)} className="rb:min-w-0 rb:flex-1 rb:cursor-pointer" aria-label={t('skills.viewSkill', { value0: item.name })}>
                            <strong>{item.name}</strong>
                            <span className="rb:mt-2 rb:block rb:text-sm rb:text-gray-600">{item.description}</span>
                          </div>
                        </Flex>
                      ))}
                    </div>
                  </section>
                </>
              ) : (
                <>
                  {member &&
                    <Button type="link" className="rb:mb-2! rb:group" onClick={() => setSelectedSkill(null)}
                      icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up_blue.svg')] rb:-rotate-90 rb:group-hover:opacity-65" />}
                    >
                      {t('skills.backToPlugin')}
                    </Button>
                  }
                  <Flex align="flex-start" gap={8} className="rb:mb-3!">
                    <Flex component="span" align="center" justify="center" className="rb:mt-4! rb:h-12 rb:w-12 rb:shrink-0 rb:rounded-lg rb:bg-blue-700" aria-hidden="true">
                      <img src={skillIcon} alt="" className="rb:h-6 rb:w-6" />
                    </Flex>
                    <div className="rb:min-w-0 rb:flex-1">
                      <div className="rb:grid rb:gap-4 rb:md:grid-cols-2">
                        <Form.Item name={selectedSkill !== null ? ['skills', selectedSkill, 'name'] : ['skill', 'name']} label={t('skills.skillName')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]} className="rb:min-w-0">
                          <Input />
                        </Form.Item>
                        <div>
                          <Form.Item name={selectedSkill !== null ? ['skills', selectedSkill, 'callName'] : ['skill', 'callName']} label={t('skills.callName')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]}
                            extra={t('skills.callNameHint')}
                          >
                            <Input />
                          </Form.Item>
                        </div>
                      </div>
                      <Form.Item name={selectedSkill !== null ? ['skills', selectedSkill, 'description'] : ['skill', 'description']} label={t('skills.skillDescription')} rules={[{ required: true, whitespace: true, message: t('common.pleaseEnter') }]}>
                        <Input.TextArea rows={3} />
                      </Form.Item>
                    </div>
                  </Flex>
                  <SkillFiles key={selectedSkill ?? 'skill'} files={files} />
                </>
              )}
            </div>
          )
        }
      </Form>
    </RbModal>
  );
}
export default SkillImport