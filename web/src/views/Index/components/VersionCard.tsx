/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-12 16:34:59
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-01-23 19:07:36
 */
import React, { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex } from 'antd';

import { getVersion, type versionResponse } from '@/api/common'
import Empty from '@/components/Empty';
import { useI18n } from '@/store/locale';

const VersionCard: React.FC = () => {
  const { t } = useTranslation();
  const { language } = useI18n()
  const [versionInfo, setVersionInfo] = useState<versionResponse | null>(null);

  // 获取当前语言对应的介绍信息
  const introduction = useMemo(() => {
    if (!versionInfo) return null;
    return language === 'zh' ? versionInfo.introduction : (versionInfo.introduction_en || versionInfo.introduction);
  }, [versionInfo, language])

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const response = await getVersion();
        setVersionInfo(response);
      } catch (error) {
        console.error('Failed to fetch version:', error);
      }
    };

    fetchVersion();
  }, []);
    
  return (
    <div className='rb:w-full rb:p-3 rb:bg-white rb:rounded-xl rb:mt-3'>
      <Flex gap={4} className="rb:mb-3">
        <span className="rb:font-[MiSans-Bold] rb:font-bold rb:leading-5">{t('index.latestUpdate')}</span>
        {versionInfo?.version && 
          <span className='rb:text-[12px] rb:text-white rb:leading-4.25 rb:pt-px rb:pl-2 rb:pr-1.75 rb:bg-[#171719] rb:rounded-lg rb:rounded-bl-none '>
            {versionInfo?.version}
          </span>
        }
      </Flex>
      {introduction
        ? (<>  
          <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mt-1 rb:mb-2">
            {t('version.releaseDate')}: {introduction.releaseDate} | {t('version.name')}: {introduction.codeName}
          </div>
          <div className="rb:max-h-76 rb:overflow-y-auto">
            <p
              className='rb:text-[12px] rb:leading-4.5'
              dangerouslySetInnerHTML={{ __html: introduction.upgradePosition }}
            />
            {introduction.coreUpgrades?.map((item: string, index: number) => (
              <p
                key={index}
                className='rb:text-[12px] rb:leading-4.5 rb:mt-2'
                dangerouslySetInnerHTML={{ __html: item }}
              />
            ))}
          </div>
        </>)
        : <Empty size={88} />
      }
    </div>
  );
};

export default VersionCard;