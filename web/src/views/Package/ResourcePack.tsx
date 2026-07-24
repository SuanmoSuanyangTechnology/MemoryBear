/**
 * ResourcePack Component
 *
 * 增购资源包（Add-on resource packs）:
 * - 左侧资源类别列表，可切换
 * - 右侧展示当前可用总额度、扩容规格选择、购买数量与合计
 * - 底部购物车条，汇总已选资源包并前往确认订单
 *
 * @component
 */
import { useMemo, useState, useRef, useEffect, type FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Button, InputNumber, Popconfirm, Tooltip, message } from 'antd';
import clsx from 'clsx';
import { CheckCircleFilled } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';

import type { ResourcePack, ResourcePackTier } from './types';
import { billingUnits, getUnit } from './constant';
import { getResourcePacks } from '@/api/package';
import PackageIcon from './PackageIcon'

/** 生成购物车条目的唯一键 */
const itemKey = (categoryKey: string, tierId: string) => `${categoryKey}_${tierId}`;


const getPackage = (key: string) => {
  return billingUnits.find((item) => item.key === key);
}
const ResourcePack: FC = () => {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();

  const isZh = i18n.language?.startsWith('zh');
  const [resourcePacks, setResourcePacks] = useState<ResourcePack[]>([]);
  const [selectedResourcePack, setSelectedResourcePack] = useState<ResourcePack | null>(null);
  const [activeSpecTier, setActiveSpecTier] = useState<ResourcePackTier | null>(null);
  const [quantity, setQuantity] = useState(1);

  const getResourcePackList = async () => {
    getResourcePacks({page: 1, pagesize: 100})
      .then(res => {
        const resourcePackList = (res as { items: ResourcePack[] }).items || [];
        setResourcePacks(resourcePackList);
        setSelectedResourcePack(resourcePackList[0]);
      })
  }
  useEffect(() => {
    getResourcePackList();
  }, []);

  useEffect(() => {
    if (selectedResourcePack) {
      setActiveSpecTier(selectedResourcePack.tiers[0]);
    }
  }, [selectedResourcePack]);

  const handleChangeTier = (tier: ResourcePackTier) => {
    const key = itemKey(selectedResourcePack?.id || '', tier.tier_id || '');
    const current = cart[key];
    setActiveSpecTier(tier);
    setQuantity(current?.tiers?.[0]?.amount || 1);
  }

  const [cart, setCart] = useState<Record<string, ResourcePack>>({});
  const cartItems = useMemo(() => Object.values(cart), [cart]);
  const cartCount = cartItems.reduce((sum, item) => sum + (item.tiers?.[0]?.amount ?? 0), 0);
  const cartTotal = cartItems.reduce((sum, item) => sum + Number(item.tiers[0].unit_price) * (item.tiers?.[0].amount || 0), 0);
  // 更新购物车
  const handleAddToCart = () => {
    if (!selectedResourcePack || !activeSpecTier) return;
    const key = itemKey(selectedResourcePack.id, activeSpecTier?.tier_id);
    setCart(prev => {
      return {
        ...prev,
        [key]: {
          ...selectedResourcePack,
          tiers: [{
            ...activeSpecTier,
            amount: quantity,
          }],
        },
      };
    });
    message.success(t('package.confirmAddCart'));
  };
  /** 清空购物车 */
  const handleClearCart = () => {
    setCart({});
    setCartExpanded(false);
    message.success(t('package.cartCleared'));
  };
  /** 从购物车移除某条目 */
  const handleRemoveCartItem = (key: string) => {
    setCart(prev => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    // 移除后若购物车已空，收起明细
    if (Object.keys(cart).length <= 1) setCartExpanded(false);
  };
  /** 修改购物车中某条目的数量 */
  const handleUpdateCartQty = (key: string, qty: number | null) => {
    const current = cart[key];
    if (current) {
      if (current.tiers[0].tier_id === activeSpecTier?.tier_id) {
        setQuantity(qty ?? 0);
      }
    }
    setCart(prev => {
      const existing = cart[key];
      if (!existing) return prev;
      let tier = existing.tiers[0]
      tier = {
        ...tier,
        amount: qty ?? 0,
      };
      return {
        ...prev,
        [key]: { ...existing, tiers: [tier] },
      };
    });
  };
  // 去下单
  const handleCheckout = () => {
    if (cartCount < 1) return;
    navigate('/order-pay', {
      state: {
        resourcePacks: cartItems,
        jumpFrom: '/resource-pack',
      },
    });
  };
  
  const [cartExpanded, setCartExpanded] = useState(false);
  /** 购物车明细弹层是否展开且有内容 */
  const cartPanelOpen = cartExpanded && cartItems.length > 0;
  /** 实测购物车明细弹层高度，用于动态计算两栏可用高度 */
  const cartPanelRef = useRef<HTMLDivElement>(null);
  const [cartPanelHeight, setCartPanelHeight] = useState(0);
  useEffect(() => {
    if (!cartPanelOpen || !cartPanelRef.current) {
      setCartPanelHeight(0);
      return;
    }
    const el = cartPanelRef.current;
    const update = () => setCartPanelHeight(el.offsetHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [cartPanelOpen, cartItems.length]);
  const handleSelectResourcePack = (resource: ResourcePack) => {
    if (resource.id === selectedResourcePack?.id) return;
    setSelectedResourcePack(resource);
    setQuantity(1);
  };

  const isHasAdd = useMemo(() => {
    return cartItems.find(item => item.id === selectedResourcePack?.id)
  }, [cartItems, selectedResourcePack])
  return (
    <Flex vertical gap={16} className="rb:relative rb:h-[calc(100vh-104px)]">
      {/* 顶部说明 */}
      <Flex justify="space-between" align="start">
        <div>
          <h2 className="rb:text-[18px] rb:font-bold rb:mb-1">{t('package.resourcePackTitle')}</h2>
          <p className="rb:text-[13px] rb:text-[#5B6167]">{t('package.resourcePackDesc')}</p>
        </div>
      </Flex>

      <Flex gap={16}
        align="stretch"
        className="rb:min-h-0"
        style={{ height: `calc(100% - 144px - ${cartPanelHeight}px)` }}
      >
        {/* 左侧类别列表 */}
        <Flex vertical gap={8} className="rb:w-[300px] rb:shrink-0 rb:min-h-0 rb:overflow-y-auto rb:pr-1">
          {resourcePacks.map(resource => {
            const active = resource.id === selectedResourcePack?.id;
            const current = cartItems.find(item => item.id === resource.id);
            const inCartQty = current?.tiers?.[0]?.amount ?? 0;
            return (
              <Tooltip key={resource.id} title={isZh ? resource.name_zh : resource.name_en}>
                <Flex
                  key={resource.id}
                  align="center"
                  gap={12}
                  className={clsx(
                    'rb:cursor-pointer rb:rounded-[10px] rb:p-3! rb:transition-colors', {
                      'rb:bg-[#171719] rb:text-white': active,
                      'rb:bg-white rb:border rb:border-[#EBEBEB] rb:hover:border-[#171719]': !active,
                    },
                  )}
                  onClick={() => handleSelectResourcePack(resource)}
                >
                  <Flex
                    align="center"
                    justify="center"
                    className="rb:shrink-0 rb:size-8 rb:rounded-lg"
                    style={{ backgroundColor: active ? 'rgba(255,255,255,0.12)' : '#F4F5F7' }}
                  >
                    <PackageIcon iconKey={getPackage(resource.billing_units[0])?.icon as string} color={active ? '#FFFFFF' : '#171719'} />
                  </Flex>
                  <div className="rb:min-w-0 rb:flex-1">
                    <div className="rb:text-[14px] rb:font-medium rb:truncate">{isZh ? resource.name_zh : resource.name_en}</div>
                    <div className={clsx('rb:text-[12px] rb:truncate', active ? 'rb:text-white/60' : 'rb:text-[#5B6167]')}>
                      {isZh ? resource.description_zh : resource.description_en}
                    </div>
                  </div>
                  {inCartQty > 0 && (
                    <Tooltip title={t('package.inCartTip', { count: inCartQty })}>
                      <Flex
                        align="center"
                        justify="center"
                        className={clsx(
                          'rb:shrink-0 rb:min-w-5 rb:h-5 rb:px-1.5! rb:rounded-full rb:text-[12px] rb:font-medium',
                          active ? 'rb:bg-white rb:text-[#171719]' : 'rb:bg-[#171719] rb:text-white',
                        )}
                      >
                        x{inCartQty}
                      </Flex>
                    </Tooltip>
                  )}
                </Flex>
              </Tooltip>
            );
          })}
        </Flex>

        {/* 右侧配置区 */}
        {selectedResourcePack &&
          <Flex vertical gap={12} justify="space-between" className="rb:flex-1 rb:min-w-0 rb:min-h-0 rb:overflow-y-auto rb:bg-white rb:rounded-[12px] rb:border rb:border-[#EBEBEB]">
            <Flex vertical gap={12}>
              {/* 类别标题 + 当前可用总额度 */}
              <Flex justify="space-between" align="center" className="rb:border-b rb:border-[#EBEBEB] rb:py-3! rb:mx-4!">
                <Flex gap={12} align="center">
                  <Flex align="center" justify="center" className="rb:size-9 rb:rounded-lg rb:bg-[#F4F5F7]">
                    <PackageIcon iconKey={getPackage(selectedResourcePack.billing_units[0])?.icon as string} color="#171719" />
                  </Flex>
                  <div>
                    <div className="rb:text-[15px] rb:font-bold">{isZh ? selectedResourcePack.name_zh : selectedResourcePack.name_en}</div>
                    <div className="rb:text-[12px] rb:text-[#5B6167]">{isZh ? selectedResourcePack.description_zh : selectedResourcePack.description_en}</div>
                  </div>
                </Flex>
                {/* <div className="rb:rounded-[10px] rb:bg-[#F7F8FA] rb:px-4 rb:py-2">
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mb-0.5">{t('package.currentTotalQuota')}</div>
                  <div className="rb:text-[16px]">
                    <span className="rb:font-bold rb:font-[MiSans-Bold]">{currentQuota ?? '--'}</span>
                    <span className="rb:font-medium">{t(`package.${activeCategory?.unit}`)}</span>
                  </div>
                </div> */}
              </Flex>

              {/* 选择扩容规格 */}
              <Flex align="baseline" gap={8} className="rb:px-3!">
                <span className="rb:text-[14px] rb:font-medium">{t('package.chooseCategory')}</span>
                <span className="rb:text-[12px] rb:text-[#5B6167]">{t('package.chooseCategoryDesc')}</span>
              </Flex>

              <div className="rb:px-3! rb:grid rb:grid-cols-3 rb:gap-3">
                {selectedResourcePack?.tiers.map(tier => {
                  const active = tier.tier_id === activeSpecTier?.tier_id;
                  return (
                    <div
                      key={tier.tier_id}
                      className={clsx(
                        'rb:relative rb:flex-1 rb:cursor-pointer rb:rounded-[10px] rb:px-4 rb:py-3 rb:transition-all',
                        active ? 'rb:border rb:border-[#171719]' : 'rb:border rb:border-[#EBEBEB] rb:hover:border-[#EBEBEB]',
                      )}
                      onClick={() => handleChangeTier(tier)}
                    >
                      {active && (
                        <CheckCircleFilled className="rb:absolute rb:top-3 rb:right-3 rb:text-[16px]!" />
                      )}
                      <div className="rb:text-[18px] rb:font-bold">
                        + {tier.quota_grants?.[selectedResourcePack?.billing_units[0]]} {t(`package.${getUnit(selectedResourcePack?.billing_units[0])}`)}
                      </div>
                      <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1 rb:mb-2">{t('package.perShareQuota')}</div>
                      <div className="rb:text-[14px] rb:font-medium">
                        ¥{tier.unit_price} <span className="rb:text-[12px] rb:text-[#5B6167]">/ {t(`package.${tier.billing_cycle}`)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </Flex>

            {/* 数量 + 合计 + 加入购物车 */}
            <Flex justify="space-between" align="center" className="rb:border-t rb:border-[#EBEBEB] rb:py-3! rb:mx-4!">
              <Flex align="center" gap={12}>
                <span className="rb:text-[13px] rb:text-[#5B6167]">{t('package.buyQuantity')}</span>
                <InputNumber
                  min={1}
                  max={99}
                  value={quantity}
                  controls={true}
                  onChange={val => setQuantity(Number(val) || 1)}
                  className="rb:w-[120px]"
                />
              </Flex>
              <Flex align="center" gap={20}>
                {activeSpecTier &&
                  <div className="rb:text-right">
                    <div className="rb:text-[12px] rb:text-[#5B6167]">
                      {t('package.addQuota')}
                      {selectedResourcePack?.billing_units.map(key => {
                        return `${Number(activeSpecTier.quota_grants?.[key] ?? 0) * quantity} ${t(`package.${getUnit(key)}`)}`;
                      }).join(' + ')}
                    </div>
                    <div className="rb:text-[20px] rb:font-bold">¥{(Number(activeSpecTier.unit_price ?? 0) * quantity).toFixed(2)}</div>
                  </div>
                }
                <Button
                  type="primary"
                  className="rb:h-10! rb:rounded-[8px]! rb:bg-[#171719]! rb:border-0! rb:hover:opacity-80!"
                  onClick={handleAddToCart}
                >
                  {isHasAdd ? t('package.confirmUpdateCart') :t('package.confirmAddCart')}
                </Button>
              </Flex>
            </Flex>
          </Flex>
        }
      </Flex>

      {/* 底部购物车条 */}
      <Flex
        align="center"
        justify="space-between"
        className="rb:absolute rb:bottom-0 rb:-left-3 rb:-right-3 rb:h-16 rb:px-5! rb:bg-white"
      >
        <Flex align="center" gap={16} className="rb:cursor-pointer" onClick={() => setCartExpanded(v => !v)}>
          <div className="rb:text-[13px] rb:text-[#5B6167]">{t('package.cartAddedItems', { count: cartItems.length })}</div>
          
          <Flex
            align="center"
            justify="center"
            className={clsx("rb:p-1! rb:rounded-[8px] rb:border rb:border-[#EBEBEB] rb:hover:border-[#171719]", {
              'rb:border-[#171719]!': cartExpanded,
            })}
          >
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
                'rb:rotate-180': cartExpanded,
              })}
            />
          </Flex>
        </Flex>
        <Flex gap={12} align="center">
          <div className="rb:text-[14px]">
            {t('package.cartTotal')} <span className="rb:text-[20px] rb:font-bold">¥ {cartTotal.toFixed(2)}</span>
          </div>
          <Button
            type="primary"
            disabled={cartCount < 1}
            className="rb:h-10! rb:rounded-[8px]! rb:bg-[#171719]! rb:border-0! rb:hover:opacity-80! rb:disabled:bg-[#EBEBEB]!"
            onClick={handleCheckout}
          >
            {t('package.goToOrder')}
          </Button>
        </Flex>
      </Flex>

      {/* 购物车明细弹出 */}
      {cartPanelOpen && (
        <div ref={cartPanelRef} className="rb:absolute rb:bottom-16 rb:-left-3 rb:-right-3 rb:bg-white rb:border-b rb:border-[#EBEBEB] rb:p-4">
          <Flex justify="space-between" align="center" className="rb:pb-3!">
            <span className="rb:text-[14px] rb:font-medium">
              {t('package.cartListTitle')}
              <span className="rb:text-[12px] rb:text-[#5B6167] rb:ml-2">{t('package.cartAddedItems', { count: cartItems.length })}</span>
            </span>

            <Flex align="center" gap={12}>
              <Popconfirm
                title={t('package.clearCartConfirm')}
                okText={t('common.confirm')}
                cancelText={t('common.cancel')}
                onConfirm={handleClearCart}
              >
                <Button type="text" size="small" danger
                  icon={<div className="rb:size-4.5 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_red.svg')]" />}
                >
                  {t('package.clearCart')}
                </Button>
              </Popconfirm>

              <div
                className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/close_grey.svg')]"
                onClick={() => setCartExpanded(v => !v)}
              />
            </Flex>
          </Flex>

          <Flex vertical gap={12} className="rb:max-h-[280px] rb:overflow-y-auto">
            {cartItems.map((item, index) => {
              const key = itemKey(item.id, item.tiers[0].tier_id);
              const tier = item.tiers[0];
              return (
                <Flex key={key} justify="space-between" align="center" gap={12}
                  className={clsx({
                    'rb:border-t rb:border-[#EBEBEB] rb:pt-3!': index > 0,
                  })}
                >
                  <Flex align="center" gap={10} className="rb:min-w-0 rb:flex-1">
                    <Flex align="center" justify="center" className="rb:shrink-0 rb:size-7 rb:rounded-lg rb:bg-[#F4F5F7]">
                    <PackageIcon iconKey={getPackage(item.billing_units[0])?.icon as string} />
                    </Flex>
                    <div className="rb:min-w-0">
                      <div className="rb:text-[13px] rb:truncate">{isZh ? item.name_zh : item.name_en}</div>
                      <div className="rb:text-[12px] rb:text-[#5B6167]">
                        {item.billing_units.map(key => {
                          return `+ ${tier.quota_grants[key]} ${t(`package.${getUnit(key)}`)} * ￥${tier.unit_price}/${t(`package.${tier.billing_cycle}`)}`
                        }).join(' + ')}
                      </div>
                    </div>
                  </Flex>
                  <InputNumber
                    min={1}
                    max={99}
                    precision={0}
                    size="small"
                    value={tier.amount}
                    controls={true}
                    className="rb:w-[92px] rb:shrink-0"
                    onChange={val => handleUpdateCartQty(key, val as number | null)}
                  />
                  <span className="rb:w-20 rb:shrink-0 rb:text-right rb:text-[13px] rb:font-medium">
                    ¥ {(Number(tier.unit_price) * (tier.amount || 0)).toFixed(2)}
                  </span>
                  <Button
                    type="text"
                    size="small"
                    className="rb:shrink-0 rb:text-[#5B6167]! rb:hover:text-[#F5222D]!"
                    icon={<div className="rb:size-4.5 rb:bg-cover rb:bg-[url('@/assets/images/common/delete_red.svg')]" />}
                    onClick={() => handleRemoveCartItem(key)}
                  />
                </Flex>
              );
            })}
          </Flex>
        </div>
      )}
    </Flex>
  );
};

export default ResourcePack;
