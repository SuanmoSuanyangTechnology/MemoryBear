/**
 * 批量导入 FAQ 使用的项目内 svg 图标。
 * 使用完整字面量 class，确保 Tailwind 能静态扫描到背景图工具类。
 */

/** 上传区大图标（export.svg） */
export const UPLOAD_ICON_CLASS = "rb:size-6 rb:bg-cover rb:bg-center rb:bg-no-repeat rb:bg-[url('@/assets/images/application/export.svg')]";

/** 按钮内小图标（export.svg） */
export const UPLOAD_ICON_SMALL_CLASS = "rb:size-4.5 rb:bg-cover rb:bg-center rb:bg-no-repeat rb:bg-[url('@/assets/images/application/export.svg')]";

/** 示例文件下载按钮图标（import.svg） */
export const TEMPLATE_ICON_CLASS = "rb:size-4.5 rb:bg-cover rb:bg-center rb:bg-no-repeat rb:bg-[url('@/assets/images/application/import.svg')] rb:group-hover/export:bg-[url('@/assets/images/application/import_blue.svg')]";
