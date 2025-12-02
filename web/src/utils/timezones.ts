import en_US from 'antd/locale/en_US';
import en_GB from 'antd/locale/en_GB';
import de_DE from 'antd/locale/de_DE';
import ru_RU from 'antd/locale/ru_RU';
import hi_IN from 'antd/locale/hi_IN';
import zh_CN from 'antd/locale/zh_CN';
import type { Locale } from 'antd/es/locale';
import 'dayjs/locale/zh'
import 'dayjs/locale/hi'
import 'dayjs/locale/ru'
import 'dayjs/locale/de'
import 'dayjs/locale/en-gb'
import 'dayjs/locale/en'

// 全世界主要时区列表
export const timezones = [

  'America/Los_Angeles', // 美国洛杉矶
  'America/New_York', // 美国纽约
  'Europe/London',    // 英国伦敦
  'Europe/Berlin',    // 德国柏林
  'Europe/Moscow',    // 俄罗斯莫斯科
  'Asia/Kolkata',     // 印度加尔各答
  'Asia/Shanghai',    // 中国上海

  // 亚洲
  // 'Asia/Tokyo',       // 日本东京
  // 'Asia/Singapore',   // 新加坡
  // 'Asia/Hong_Kong',   // 中国香港
  // 'Asia/Taipei',      // 中国台北
  // 'Asia/Seoul',       // 韩国首尔
  // 'Asia/Bangkok',     // 泰国曼谷
  // 'Asia/Jakarta',     // 印度尼西亚雅加达
  // 'Asia/Manila',      // 菲律宾马尼拉
  // 'Asia/Dubai',       // 阿联酋迪拜
  // 'Asia/Tashkent',    // 乌兹别克斯坦塔什干
  // 'Asia/Riyadh',      // 沙特阿拉伯利雅得
  // 'Asia/Baku',        // 阿塞拜疆巴库
  // 'Asia/Istanbul',    // 土耳其伊斯坦布尔
  
  // 欧洲
  // 'Europe/Paris',     // 法国巴黎
  // 'Europe/Rome',      // 意大利罗马
  // 'Europe/Madrid',    // 西班牙马德里
  // 'Europe/Amsterdam', // 荷兰阿姆斯特丹
  // 'Europe/Vienna',    // 奥地利维也纳
  // 'Europe/Stockholm', // 瑞典斯德哥尔摩
  // 'Europe/Oslo',      // 挪威奥斯陆
  // 'Europe/Copenhagen', // 丹麦哥本哈根
  // 'Europe/Zurich',    // 瑞士苏黎世
  // 'Europe/Athens',    // 希腊雅典
  // 'Europe/Warsaw',    // 波兰华沙
  // 'Europe/Prague',    // 捷克布拉格
  // 'Europe/Budapest',  // 匈牙利布达佩斯
  // 'Europe/Belgrade',  // 塞尔维亚贝尔格莱德
  
  // 北美洲
  // 'America/Chicago',  // 美国芝加哥
  // 'America/Denver',   // 美国丹佛
  // 'America/Toronto',  // 加拿大多伦多
  // 'America/Vancouver', // 加拿大温哥华
  // 'America/Mexico_City', // 墨西哥墨西哥城
  
  // 南美洲
  // 'America/Sao_Paulo', // 巴西圣保罗
  // 'America/Buenos_Aires', // 阿根廷布宜诺斯艾利斯
  // 'America/Santiago', // 智利圣地亚哥
  // 'America/Lima',     // 秘鲁利马
  // 'America/Bogota',   // 哥伦比亚波哥大
  // 'America/Caracas',  // 委内瑞拉加拉加斯
  
  // // 大洋洲
  // 'Australia/Sydney', // 澳大利亚悉尼
  // 'Australia/Melbourne', // 澳大利亚墨尔本
  // 'Australia/Brisbane', // 澳大利亚布里斯班
  // 'Australia/Perth',  // 澳大利亚珀斯
  // 'New_Zealand/Auckland', // 新西兰奥克兰
  
  // // 非洲
  // 'Africa/Cairo',     // 埃及开罗
  // 'Africa/Johannesburg', // 南非约翰内斯堡
  // 'Africa/Lagos',     // 尼日利亚拉各斯
  // 'Africa/Casablanca', // 摩洛哥卡萨布兰卡
  // 'Africa/Nairobi',   // 肯尼亚内罗毕
  // 'Africa/Addis_Ababa', // 埃塞俄比亚亚的斯亚贝巴
  
  // // 其他
  // 'UTC',              // 协调世界时
];

// 注意：时区显示名称已移至i18n翻译文件中（zh.ts和en.ts）
// 请使用i18n.t('timezones.时区名称')来获取本地化的时区显示名称

// 时区与antd本地化文件的映射
// 键为时区，值为antd本地化文件的名称
export const timezoneToAntdLocaleMap: Record<string, Locale> = {
  // 亚洲
  'Asia/Shanghai': zh_CN,     // 中国上海 - 中文(中国大陆)
  'Asia/Kolkata': hi_IN,      // 印度加尔各答 - 印地语
  'Europe/Moscow': ru_RU,     // 俄罗斯莫斯科 - 俄语
  'Europe/Berlin': de_DE,     // 德国柏林 - 德语
  'Europe/London': en_GB,     // 英国伦敦 - 英语(英国)
  'America/New_York': en_US,  // 美国纽约 - 英语(美国)
  'America/Los_Angeles': en_US, // 美国洛杉矶 - 英语(美国)

  // 'Asia/Tokyo': 'ja_JP',        // 日本东京 - 日语
  // 'Asia/Singapore': 'en_SG',    // 新加坡 - 英语(新加坡)
  // 'Asia/Hong_Kong': 'zh_HK',    // 中国香港 - 中文(香港)
  // 'Asia/Taipei': 'zh_TW',       // 中国台北 - 中文(台湾)
  // 'Asia/Seoul': 'ko_KR',        // 韩国首尔 - 韩语
  // 'Asia/Bangkok': 'th_TH',      // 泰国曼谷 - 泰语
  // 'Asia/Jakarta': 'id_ID',      // 印度尼西亚雅加达 - 印尼语
  // 'Asia/Manila': 'en_PH',       // 菲律宾马尼拉 - 英语(菲律宾)
  // 'Asia/Dubai': 'ar_AE',        // 阿联酋迪拜 - 阿拉伯语
  // 'Asia/Tashkent': 'uz_UZ',     // 乌兹别克斯坦塔什干 - 乌兹别克语
  // 'Asia/Riyadh': 'ar_SA',       // 沙特阿拉伯利雅得 - 阿拉伯语
  // 'Asia/Baku': 'az_AZ',         // 阿塞拜疆巴库 - 阿塞拜疆语
  // 'Asia/Istanbul': 'tr_TR',     // 土耳其伊斯坦布尔 - 土耳其语
  
  // // 欧洲
  // 'Europe/Paris': 'fr_FR',      // 法国巴黎 - 法语
  // 'Europe/Rome': 'it_IT',       // 意大利罗马 - 意大利语
  // 'Europe/Madrid': 'es_ES',     // 西班牙马德里 - 西班牙语
  // 'Europe/Amsterdam': 'nl_NL',  // 荷兰阿姆斯特丹 - 荷兰语
  // 'Europe/Vienna': 'de_AT',     // 奥地利维也纳 - 德语(奥地利)
  // 'Europe/Stockholm': 'sv_SE',  // 瑞典斯德哥尔摩 - 瑞典语
  // 'Europe/Oslo': 'nb_NO',       // 挪威奥斯陆 - 挪威语
  // 'Europe/Copenhagen': 'da_DK', // 丹麦哥本哈根 - 丹麦语
  // 'Europe/Zurich': 'de_CH',     // 瑞士苏黎世 - 德语(瑞士)
  // 'Europe/Athens': 'el_GR',     // 希腊雅典 - 希腊语
  // 'Europe/Warsaw': 'pl_PL',     // 波兰华沙 - 波兰语
  // 'Europe/Prague': 'cs_CZ',     // 捷克布拉格 - 捷克语
  // 'Europe/Budapest': 'hu_HU',   // 匈牙利布达佩斯 - 匈牙利语
  // 'Europe/Belgrade': 'sr_RS',   // 塞尔维亚贝尔格莱德 - 塞尔维亚语
  
  // // 北美洲
  // 'America/Chicago': 'en_US',   // 美国芝加哥 - 英语(美国)
  // 'America/Denver': 'en_US',    // 美国丹佛 - 英语(美国)
  // 'America/Toronto': 'en_CA',   // 加拿大多伦多 - 英语(加拿大)
  // 'America/Vancouver': 'en_CA', // 加拿大温哥华 - 英语(加拿大)
  // 'America/Mexico_City': 'es_MX', // 墨西哥墨西哥城 - 西班牙语(墨西哥)
  
  // // 南美洲
  // 'America/Sao_Paulo': 'pt_BR', // 巴西圣保罗 - 葡萄牙语(巴西)
  // 'America/Buenos_Aires': 'es_AR', // 阿根廷布宜诺斯艾利斯 - 西班牙语(阿根廷)
  // 'America/Santiago': 'es_CL',  // 智利圣地亚哥 - 西班牙语(智利)
  // 'America/Lima': 'es_PE',      // 秘鲁利马 - 西班牙语(秘鲁)
  // 'America/Bogota': 'es_CO',    // 哥伦比亚波哥大 - 西班牙语(哥伦比亚)
  // 'America/Caracas': 'es_VE',   // 委内瑞拉加拉加斯 - 西班牙语(委内瑞拉)
  
  // // 大洋洲
  // 'Australia/Sydney': 'en_AU',  // 澳大利亚悉尼 - 英语(澳大利亚)
  // 'Australia/Melbourne': 'en_AU', // 澳大利亚墨尔本 - 英语(澳大利亚)
  // 'Australia/Brisbane': 'en_AU', // 澳大利亚布里斯班 - 英语(澳大利亚)
  // 'Australia/Perth': 'en_AU',   // 澳大利亚珀斯 - 英语(澳大利亚)
  // 'New_Zealand/Auckland': 'en_NZ', // 新西兰奥克兰 - 英语(新西兰)
  
  // // 非洲
  // 'Africa/Cairo': 'ar_EG',      // 埃及开罗 - 阿拉伯语(埃及)
  // 'Africa/Johannesburg': 'en_ZA', // 南非约翰内斯堡 - 英语(南非)
  // 'Africa/Lagos': 'en_NG',      // 尼日利亚拉各斯 - 英语(尼日利亚)
  // 'Africa/Casablanca': 'fr_MA', // 摩洛哥卡萨布兰卡 - 法语(摩洛哥)
  // 'Africa/Nairobi': 'en_KE',    // 肯尼亚内罗毕 - 英语(肯尼亚)
  // 'Africa/Addis_Ababa': 'am_ET', // 埃塞俄比亚亚的斯亚贝巴 - 阿姆哈拉语
  
  // // 其他
  // 'UTC': 'en_US',               // 协调世界时 - 默认英语(美国)
};