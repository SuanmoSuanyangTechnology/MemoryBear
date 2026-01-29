import type { ThemeConfig } from 'antd';

// 浅色主题配置
export const lightTheme: ThemeConfig = {
  token: {
    colorPrimary: '#155EEF',
    colorBgBase: '#ffffff',
    colorTextBase: '#212332',
    colorBorder: '#DFE4ED',
    colorBgLayout: '#ffffff',
    colorBgContainer: '#ffffff',
    colorText: '#212332',
    colorTextSecondary: '#6b7280',
    borderRadius: 6,
    colorSplit: '#DFE4ED',
    colorBorderBg: '#DFE4ED',
    colorBgContainerDisabled: '#F6F8FC',
    colorTextDisabled: '#5B6167',
    // Card 用到
    borderRadiusLG: 12,
    colorBorderSecondary: '#DFE4ED',
    // colorBgContainer: '#FBFDFF',
    colorError: '#FF5D34',
    sizeSM: 12,
    fontSizeSM: 12,
  },
  components: {
    Layout: {
      headerBg: 'transparent',
      bodyBg: '#FBFDFF',
      siderBg: 'transparent',
      headerPadding: '16px 46px 16px 21px',
      headerHeight: 64,
      headerColor: '#212332',
    },
    Menu: {
      itemColor: '#5B6167',
      itemSelectedColor: '#212332',
      subMenuItemSelectedColor: '#212332',

      itemHoverColor: '#212332',
      itemHoverBg: '#FFFFFF',

      itemBg: 'transparent',
      itemSelectedBg: '#FFFFFF',

      subMenuItemBg: 'transparent',

      itemPaddingInline: 12,
      
      itemHeight: 32,
      itemMarginBlock: 8,
      horizontalLineHeight: 32,
      itemMarginInline: 12,
      itemBorderRadius: 6,

      iconSize: 16,
      iconMarginInlineEnd: 8,
      collapsedIconSize: 12,
      collapsedWidth: '64px',

      popupBg: '#FBFDFF',
    },
    Button: {
      defaultColor: '#5B6167',
      defaultBorderColor: '#EBEBEB',
      defaultShadow: 'none',
      primaryShadow: 'none',
      dangerShadow: 'none'
    },
    Form: {
      labelColor: '#212332',
    },
    Slider: {
      // dotSize: 10,
      controlSize: 8,
      railSize: 8,
      handleSize: 10,
      handleSizeHover: 10,
      handleColor: '#155EEF',
      trackBg: '#155EEF',
      railBg: '#E1E2E7',
    },
    Table: {
      borderColor: '#DFE4ED',
      headerBg: '#FBFDFF',
      rowHoverBg: '#F0F3F8',
      rowSelectedBg: '#E9F1FF',
      rowSelectedHoverBg: '#F0F3F8',
      cellPaddingBlock: 8,
      cellFontSizeSM: 12,

      // cellPaddingInline: 24,
      selectionColumnWidth: 48,
    },
    Breadcrumb: {
      itemColor: '#5B6167',
      lastItemColor: '#212332',
      linkColor: '#5B6167',
      linkHoverColor: '#212332',
    },
    Input: {
      inputFontSizeSM: 12,
      controlHeightSM: 26
    },
    Select: {
      lineHeightSM: 26
    },
    Upload: {
      pictureCardSize: 96,
    }
  }
};