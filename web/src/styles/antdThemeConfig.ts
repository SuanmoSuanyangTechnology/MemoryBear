import type { ThemeConfig } from 'antd';

// 浅色主题配置
export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: '#171719',
    colorBgBase: '#ffffff',
    colorTextBase: '#171719',
    colorBorder: '#DFE4ED',
    colorBgLayout: '#ffffff',
    colorBgContainer: '#ffffff',
    colorText: '#171719',
    colorTextSecondary: '#6b7280',
    borderRadius: 8,
    colorSplit: '#DFE4ED',
    colorBorderBg: '#DFE4ED',
    colorBgContainerDisabled: '#F6F6F6',
    colorTextDisabled: '#5B6167',
    // Card 用到
    borderRadiusLG: 12,
    borderRadiusSM: 8,
    colorBorderSecondary: '#DFE4ED',
    // colorBgContainer: '#FBFDFF',
    colorError: '#FF5D34',
    sizeSM: 12,
    fontSizeSM: 12,
    boxShadow: 'none',
  },
  components: {
    Layout: {
      headerBg: 'transparent',
      bodyBg: '#EEEFF4',
      siderBg: '#FAFCFF',
      headerPadding: '0 24px 0 20px',
      headerHeight: 64,
      headerColor: '#212332',
    },
    Menu: {
      fontSize: 13,
      itemColor: '#171719',
      itemSelectedColor: '#FFFFFF',
      subMenuItemSelectedColor: '#FFFFFF',

      itemHoverColor: '#171719',
      itemHoverBg: 'rgba(223,228,237,0.5)',

      itemBg: 'transparent',
      itemSelectedBg: '#171719',

      subMenuItemBg: 'transparent',

      itemPaddingInline: 10,

      itemHeight: 38,
      itemMarginBlock: 8,
      horizontalLineHeight: 32,
      itemMarginInline: 12,
      itemBorderRadius: 8,

      iconSize: 16,
      iconMarginInlineEnd: 10,
      collapsedIconSize: 12,
      collapsedWidth: '64px',

      popupBg: '#FBFDFF',
      groupTitleFontSize: 12,
      groupTitleColor: '#5B6167',
      groupTitleLineHeight: '17px',
    },
    Button: {
      defaultColor: '#171719',
      defaultBorderColor: '#EBEBEB',
      defaultShadow: 'none',
      primaryShadow: 'none',
      dangerShadow: 'none',
      defaultGhostBorderColor: '#EBEBEB',
      defaultHoverColor: 'rgba(23, 23, 25, 0.7)',
      defaultHoverBorderColor: 'rgba(23, 23, 25, 0.7)',
    },
    Form: {
      labelColor: '#171719',
      itemMarginBottom: 16,
    },
    Slider: {
      // dotSize: 10,
      controlSize: 6,
      railSize: 6,
      handleSize: 10,
      handleSizeHover: 10,
      handleColor: '#171719',
      handleActiveOutlineColor: '#171719',
      trackBg: '#171719',
      railBg: '#EBEBEB',
    },
    Table: {
      headerColor: '#5B6167',
      borderColor: '#EBEBEB',
      headerBg: '#FFFFFF',
      rowHoverBg: '#F0F3F8',
      rowSelectedBg: '#E9F1FF',
      rowSelectedHoverBg: '#F0F3F8',
      cellPaddingBlock: 8,
      cellFontSizeSM: 12,
      footerBg: '#FFFFFF',
      colorText: '#5B6167',

      // cellPaddingInline: 24,
      selectionColumnWidth: 48,
    },
    Breadcrumb: {
      itemColor: '#5B6167',
      lastItemColor: '#171719',
      linkColor: '#5B6167',
      linkHoverColor: '#171719',
      fontSize: 18,
    },
    Input: {
      inputFontSizeSM: 12,
      controlHeightSM: 26,
      activeShadow: 'none',
    },
    InputNumber: {
      activeShadow: 'none',
    },
    Select: {
      lineHeightSM: 26,
    },
    Upload: {
      pictureCardSize: 96,
    },
    Switch: {
      trackHeight: 24,
      trackHeightSM: 18,
      handleSize: 20,
      handleSizeSM: 14,
      innerMinMarginSM: 8,
      innerMaxMarginSM: 27,
    },
    Cascader: {
      optionSelectedBg: '#F6F6F6',
      optionSelectedColor: '#212332'
    },
    Statistic: {
      contentFontSize: 14,
      titleFontSize: 14
    },
    Progress: {
      remainingColor: '#EBEBEB',
    },
    Segmented: {
      trackBg: '#E1E2E7',
    }
  }
};