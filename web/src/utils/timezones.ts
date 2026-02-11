/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-02 16:37:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-02 16:37:10 
 */
/**
 * Timezone Configuration Module
 * 
 * Provides:
 * - Major world timezone list
 * - Timezone to Ant Design locale mapping
 * - Dayjs locale imports
 * 
 * Note: Timezone display names are in i18n translation files (zh.ts and en.ts)
 * Use i18n.t('timezones.timezone_name') to get localized timezone display names
 * 
 * @module timezones
 */

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

/**
 * Major world timezones list
 */
export const timezones = [

  'America/Los_Angeles', // Los Angeles, USA
  'America/New_York', // New York, USA
  'Europe/London',    // London, UK
  'Europe/Berlin',    // Berlin, Germany
  'Europe/Moscow',    // Moscow, Russia
  'Asia/Kolkata',     // Kolkata, India
  'Asia/Shanghai',    // Shanghai, China

  // Asia
  // 'Asia/Tokyo',       // Tokyo, Japan
  // 'Asia/Singapore',   // Singapore
  // 'Asia/Hong_Kong',   // Hong Kong, China
  // 'Asia/Taipei',      // Taipei, Taiwan
  // 'Asia/Seoul',       // Seoul, South Korea
  // 'Asia/Bangkok',     // Bangkok, Thailand
  // 'Asia/Jakarta',     // Jakarta, Indonesia
  // 'Asia/Manila',      // Manila, Philippines
  // 'Asia/Dubai',       // Dubai, UAE
  // 'Asia/Tashkent',    // Tashkent, Uzbekistan
  // 'Asia/Riyadh',      // Riyadh, Saudi Arabia
  // 'Asia/Baku',        // Baku, Azerbaijan
  // 'Asia/Istanbul',    // Istanbul, Turkey
  
  // Europe
  // 'Europe/Paris',     // Paris, France
  // 'Europe/Rome',      // Rome, Italy
  // 'Europe/Madrid',    // Madrid, Spain
  // 'Europe/Amsterdam', // Amsterdam, Netherlands
  // 'Europe/Vienna',    // Vienna, Austria
  // 'Europe/Stockholm', // Stockholm, Sweden
  // 'Europe/Oslo',      // Oslo, Norway
  // 'Europe/Copenhagen', // Copenhagen, Denmark
  // 'Europe/Zurich',    // Zurich, Switzerland
  // 'Europe/Athens',    // Athens, Greece
  // 'Europe/Warsaw',    // Warsaw, Poland
  // 'Europe/Prague',    // Prague, Czech Republic
  // 'Europe/Budapest',  // Budapest, Hungary
  // 'Europe/Belgrade',  // Belgrade, Serbia
  
  // North America
  // 'America/Chicago',  // Chicago, USA
  // 'America/Denver',   // Denver, USA
  // 'America/Toronto',  // Toronto, Canada
  // 'America/Vancouver', // Vancouver, Canada
  // 'America/Mexico_City', // Mexico City, Mexico
  
  // South America
  // 'America/Sao_Paulo', // São Paulo, Brazil
  // 'America/Buenos_Aires', // Buenos Aires, Argentina
  // 'America/Santiago', // Santiago, Chile
  // 'America/Lima',     // Lima, Peru
  // 'America/Bogota',   // Bogotá, Colombia
  // 'America/Caracas',  // Caracas, Venezuela
  
  // Oceania
  // 'Australia/Sydney', // Sydney, Australia
  // 'Australia/Melbourne', // Melbourne, Australia
  // 'Australia/Brisbane', // Brisbane, Australia
  // 'Australia/Perth',  // Perth, Australia
  // 'New_Zealand/Auckland', // Auckland, New Zealand
  
  // Africa
  // 'Africa/Cairo',     // Cairo, Egypt
  // 'Africa/Johannesburg', // Johannesburg, South Africa
  // 'Africa/Lagos',     // Lagos, Nigeria
  // 'Africa/Casablanca', // Casablanca, Morocco
  // 'Africa/Nairobi',   // Nairobi, Kenya
  // 'Africa/Addis_Ababa', // Addis Ababa, Ethiopia
  
  // Other
  // 'UTC',              // Coordinated Universal Time
];

/**
 * Timezone to Ant Design locale mapping
 * Key: timezone identifier
 * Value: Ant Design locale object
 */
export const timezoneToAntdLocaleMap: Record<string, Locale> = {
  // Asia
  'Asia/Shanghai': zh_CN,     // Shanghai, China - Chinese (Mainland)
  'Asia/Kolkata': hi_IN,      // Kolkata, India - Hindi
  'Europe/Moscow': ru_RU,     // Moscow, Russia - Russian
  'Europe/Berlin': de_DE,     // Berlin, Germany - German
  'Europe/London': en_GB,     // London, UK - English (UK)
  'America/New_York': en_US,  // New York, USA - English (US)
  'America/Los_Angeles': en_US, // Los Angeles, USA - English (US)

  // 'Asia/Tokyo': 'ja_JP',        // Tokyo, Japan - Japanese
  // 'Asia/Singapore': 'en_SG',    // Singapore - English (Singapore)
  // 'Asia/Hong_Kong': 'zh_HK',    // Hong Kong, China - Chinese (Hong Kong)
  // 'Asia/Taipei': 'zh_TW',       // Taipei, Taiwan - Chinese (Taiwan)
  // 'Asia/Seoul': 'ko_KR',        // Seoul, South Korea - Korean
  // 'Asia/Bangkok': 'th_TH',      // Bangkok, Thailand - Thai
  // 'Asia/Jakarta': 'id_ID',      // Jakarta, Indonesia - Indonesian
  // 'Asia/Manila': 'en_PH',       // Manila, Philippines - English (Philippines)
  // 'Asia/Dubai': 'ar_AE',        // Dubai, UAE - Arabic
  // 'Asia/Tashkent': 'uz_UZ',     // Tashkent, Uzbekistan - Uzbek
  // 'Asia/Riyadh': 'ar_SA',       // Riyadh, Saudi Arabia - Arabic
  // 'Asia/Baku': 'az_AZ',         // Baku, Azerbaijan - Azerbaijani
  // 'Asia/Istanbul': 'tr_TR',     // Istanbul, Turkey - Turkish
  
  // Europe
  // 'Europe/Paris': 'fr_FR',      // Paris, France - French
  // 'Europe/Rome': 'it_IT',       // Rome, Italy - Italian
  // 'Europe/Madrid': 'es_ES',     // Madrid, Spain - Spanish
  // 'Europe/Amsterdam': 'nl_NL',  // Amsterdam, Netherlands - Dutch
  // 'Europe/Vienna': 'de_AT',     // Vienna, Austria - German (Austria)
  // 'Europe/Stockholm': 'sv_SE',  // Stockholm, Sweden - Swedish
  // 'Europe/Oslo': 'nb_NO',       // Oslo, Norway - Norwegian
  // 'Europe/Copenhagen': 'da_DK', // Copenhagen, Denmark - Danish
  // 'Europe/Zurich': 'de_CH',     // Zurich, Switzerland - German (Switzerland)
  // 'Europe/Athens': 'el_GR',     // Athens, Greece - Greek
  // 'Europe/Warsaw': 'pl_PL',     // Warsaw, Poland - Polish
  // 'Europe/Prague': 'cs_CZ',     // Prague, Czech Republic - Czech
  // 'Europe/Budapest': 'hu_HU',   // Budapest, Hungary - Hungarian
  // 'Europe/Belgrade': 'sr_RS',   // Belgrade, Serbia - Serbian
  
  // North America
  // 'America/Chicago': 'en_US',   // Chicago, USA - English (US)
  // 'America/Denver': 'en_US',    // Denver, USA - English (US)
  // 'America/Toronto': 'en_CA',   // Toronto, Canada - English (Canada)
  // 'America/Vancouver': 'en_CA', // Vancouver, Canada - English (Canada)
  // 'America/Mexico_City': 'es_MX', // Mexico City, Mexico - Spanish (Mexico)
  
  // South America
  // 'America/Sao_Paulo': 'pt_BR', // São Paulo, Brazil - Portuguese (Brazil)
  // 'America/Buenos_Aires': 'es_AR', // Buenos Aires, Argentina - Spanish (Argentina)
  // 'America/Santiago': 'es_CL',  // Santiago, Chile - Spanish (Chile)
  // 'America/Lima': 'es_PE',      // Lima, Peru - Spanish (Peru)
  // 'America/Bogota': 'es_CO',    // Bogotá, Colombia - Spanish (Colombia)
  // 'America/Caracas': 'es_VE',   // Caracas, Venezuela - Spanish (Venezuela)
  
  // Oceania
  // 'Australia/Sydney': 'en_AU',  // Sydney, Australia - English (Australia)
  // 'Australia/Melbourne': 'en_AU', // Melbourne, Australia - English (Australia)
  // 'Australia/Brisbane': 'en_AU', // Brisbane, Australia - English (Australia)
  // 'Australia/Perth': 'en_AU',   // Perth, Australia - English (Australia)
  // 'New_Zealand/Auckland': 'en_NZ', // Auckland, New Zealand - English (New Zealand)
  
  // Africa
  // 'Africa/Cairo': 'ar_EG',      // Cairo, Egypt - Arabic (Egypt)
  // 'Africa/Johannesburg': 'en_ZA', // Johannesburg, South Africa - English (South Africa)
  // 'Africa/Lagos': 'en_NG',      // Lagos, Nigeria - English (Nigeria)
  // 'Africa/Casablanca': 'fr_MA', // Casablanca, Morocco - French (Morocco)
  // 'Africa/Nairobi': 'en_KE',    // Nairobi, Kenya - English (Kenya)
  // 'Africa/Addis_Ababa': 'am_ET', // Addis Ababa, Ethiopia - Amharic
  
  // Other
  // 'UTC': 'en_US',               // Coordinated Universal Time - Default English (US)
};