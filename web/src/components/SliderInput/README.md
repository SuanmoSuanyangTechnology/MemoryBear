# SliderInput 组件

组合了 Slider 和 InputNumber 的组件，支持拖拽和输入两种方式设置数值。

## 功能特性

- 同时支持滑块拖拽和数字输入
- 自动同步两个控件的值
- 支持最小值、最大值、步长设置
- 支持自定义标记点
- 支持禁用状态
- 响应式布局

## 使用方法

### 基础用法

```tsx
import SliderInput from '@/components/SliderInput';

function MyComponent() {
  const [blockSize, setBlockSize] = useState(500);

  return (
    <SliderInput 
      value={blockSize}
      onChange={setBlockSize}
      min={100}
      max={2000}
      step={50}
    />
  );
}
```

### 带标签

```tsx
<SliderInput 
  label="块大小 (Block Size)"
  value={blockSize}
  onChange={setBlockSize}
  min={100}
  max={2000}
  step={50}
/>
```

### 带标记点

```tsx
<SliderInput 
  label="块大小"
  value={blockSize}
  onChange={setBlockSize}
  min={100}
  max={2000}
  step={50}
  marks={{
    100: '100',
    500: '500',
    1000: '1000',
    2000: '2000',
  }}
/>
```

### 自定义标记点样式

```tsx
<SliderInput 
  label="块大小"
  value={blockSize}
  onChange={setBlockSize}
  min={100}
  max={2000}
  step={50}
  marks={{
    100: { style: { color: '#f50' }, label: '最小' },
    1000: { style: { color: '#1890ff' }, label: '推荐' },
    2000: { style: { color: '#f50' }, label: '最大' },
  }}
/>
```

### 带提示信息

```tsx
<SliderInput 
  label="块大小"
  value={blockSize}
  onChange={setBlockSize}
  min={100}
  max={2000}
  step={50}
  tooltip={{
    open: true,
    placement: 'top',
    formatter: (value) => `${value} 字符`,
  }}
/>
```

### 在表单中使用

```tsx
import { Form } from 'antd';
import SliderInput from '@/components/SliderInput';

function FormExample() {
  const [form] = Form.useForm();

  return (
    <Form form={form}>
      <Form.Item 
        name="blockSize" 
        label="块大小"
        initialValue={500}
      >
        <SliderInput 
          min={100}
          max={2000}
          step={50}
        />
      </Form.Item>
    </Form>
  );
}
```

### 在 CreateDataset 中使用

```tsx
import SliderInput from '@/components/SliderInput';

const CreateDataset = () => {
  const [blockSize, setBlockSize] = useState(500);
  const [overlap, setOverlap] = useState(50);

  return (
    <div className='rb:flex rb:flex-col rb:gap-6'>
      <SliderInput 
        label={t('knowledgeBase.blockSize') || '块大小'}
        value={blockSize}
        onChange={setBlockSize}
        min={100}
        max={2000}
        step={50}
        marks={{
          100: '100',
          500: '500',
          1000: '1000',
          2000: '2000',
        }}
      />
      
      <SliderInput 
        label={t('knowledgeBase.overlap') || '重叠大小'}
        value={overlap}
        onChange={setOverlap}
        min={0}
        max={200}
        step={10}
        marks={{
          0: '0',
          50: '50',
          100: '100',
          200: '200',
        }}
      />
    </div>
  );
}
```

## Props

| 属性 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| value | number | - | 当前值（受控） |
| onChange | (value: number \| null) => void | - | 值变化时的回调函数 |
| min | number | 0 | 最小值 |
| max | number | 100 | 最大值 |
| step | number | 1 | 步长 |
| defaultValue | number | 0 | 默认值（非受控） |
| disabled | boolean | false | 是否禁用 |
| label | string | - | 标签文本 |
| className | string | '' | 容器自定义样式类名 |
| sliderClassName | string | '' | Slider 自定义样式类名 |
| inputClassName | string | '' | InputNumber 自定义样式类名 |
| marks | Record<number, string \| object> | - | 刻度标记 |
| tooltip | object | - | 提示信息配置 |

### tooltip 配置

| 属性 | 类型 | 说明 |
|------|------|------|
| open | boolean | 是否始终显示提示 |
| placement | 'top' \| 'left' \| 'right' \| 'bottom' | 提示位置 |
| formatter | (value?: number) => ReactNode | 格式化提示内容 |

## 注意事项

1. **值的范围**：输入的值会自动限制在 min 和 max 之间
2. **步长**：拖拽和输入都会遵循 step 设置
3. **受控组件**：建议使用受控模式（传入 value 和 onChange）
4. **布局**：Slider 占据剩余空间，InputNumber 固定宽度 120px

## 样式定制

### 自定义宽度

```tsx
<SliderInput 
  value={blockSize}
  onChange={setBlockSize}
  className="rb:max-w-2xl"
/>
```

### 自定义 InputNumber 宽度

```tsx
<SliderInput 
  value={blockSize}
  onChange={setBlockSize}
  inputClassName="rb:w-32"
/>
```

## 常见场景

### 场景1：文档分块大小设置

```tsx
<SliderInput 
  label="块大小 (字符数)"
  value={chunkSize}
  onChange={setChunkSize}
  min={100}
  max={2000}
  step={50}
  defaultValue={500}
  marks={{
    100: '最小',
    500: '推荐',
    2000: '最大',
  }}
  tooltip={{
    formatter: (value) => `${value} 字符`,
  }}
/>
```

### 场景2：重叠大小设置

```tsx
<SliderInput 
  label="重叠大小 (字符数)"
  value={overlap}
  onChange={setOverlap}
  min={0}
  max={Math.floor(chunkSize * 0.5)}
  step={10}
  defaultValue={50}
  tooltip={{
    formatter: (value) => `${value} 字符 (${((value / chunkSize) * 100).toFixed(0)}%)`,
  }}
/>
```

### 场景3：温度参数设置

```tsx
<SliderInput 
  label="Temperature"
  value={temperature}
  onChange={setTemperature}
  min={0}
  max={2}
  step={0.1}
  defaultValue={0.7}
  marks={{
    0: '精确',
    0.7: '平衡',
    2: '创造',
  }}
/>
```
