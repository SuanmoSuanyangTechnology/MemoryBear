# API接口

```javascript
search_switch：0（深度检索需要验证）
search_switch：1（检索不需要验证）
search_switch：2（快速回复，只根据上下文回复）
code:200. 状态正常
code:500. 状态异常
```

# 一：日志

### 请求端口http://{服务器}:9200/api/memory/download_log

### 请求方式：GET

### 描述

下载系统运行日志或最近一次任务的日志文本，辅助问题排查。支持两种模式：
- `log_type=file`（默认）：返回完整日志文件内容
- `log_type=transmission`：实时流式传输日志（SSE）

### 输入：

| 参数名 | 类型 | 是否必填 | 描述 |
| --- | --- | --- | --- |
| log_type | string | 否 | 日志类型：`file`（完整文件，默认）或 `transmission`（流式传输） |

### 输出

```javascript
{
      "code": 状态,
      "msg": '',
      "data": 文本内容,
      "error": "报错信息",
      "time": 当前时间戳
    }
```

# 二：引擎状态

### 请求端口http://{服务器}:9200/api/memory/health/status

### 请求方式：GET

### 描述

获取引擎健康状态与服务可用性信息，用于在线监控。

### 输入：无

### 输出

```javascript
{
      "code": 状态,
      "msg": '',
      "data": 文本内容,
      "error": "报错信息",
      "time": 当前时间戳
    }
```

# 三：读取接口

### 请求端口http://{服务器的IP}:9200/api/memory/read_service

### 请求方式：POST

### 描述

获取指定用户的记忆容量、活跃度、知识资产总数等概览信息。

### 输入：

```javascript
{
    "user_id":"用户会话的唯一ID",
    "message":存储内容,
    "history":[]
   "search_switch":'2'
}
```

### 请求体参数

| 参数名 | 类型 | 是否必填 | 描述 |
| --- | --- | --- | --- |
| group_id | string | 是 | 会话组唯一标识 |
| message | string | 是 | 用户输入消息 |
| history | array of object | 否 | 历史对话列表 |
| search_switch | string | 是 | 检索模式：`0`需验证、`1`无验证、`2`仅上下文回复 |

### 输出：

```javascript
{
    "code": 0,
    "msg": "写入成功",
    "data": "success",
    "error": "",
    "time": 1762169014
}
```

# 四：存储接口

### 请求端口http://{服务器的IP}:9200/api/memory/writer_service

### 请求方式：POST

### 描述

写入用户对话消息或内容片段，持久化到系统存储。

### 输入：

```javascript
{
    "user_id":用户会话的唯一ID,
    "message":输入内容, 
    "history":[],
}
```

### 请求体参数

| 参数名 | 类型 | 是否必填 | 描述 |
| --- | --- | --- | --- |
| group_id | string | 是 | 会话组唯一标识 |
| message | string | 是 | 待写入内容 |

### 输出

```javascript
{
    "code": 0,
    "msg": "回复对话消息成功",
    "data": "张曼玉喜欢画画，尤其是水彩画，并且她还喜欢吃各种食物，比如排骨和玉米。最近她在尝试水彩画。",
    "error": "",
    "time": 1762168079
}
```

# 五：数据类型

### 请求端口http://{服务器的IP}:9200/api/memory/status_type

### 请求方式：POST

### 描述

判别输入内容的数据类型，区分读取/写入等业务意图。

### 输入：

```javascript
{
    "message":输入内容, 
}
```

### 请求体参数

| 参数名 | 类型 | 是否必填 | 描述 |
| --- | --- | --- | --- |
| message | string | 是 | 用户输入内容 |
| group_id | string | 是 | 会话组唯一标识 |

### 输出

```javascript
{
    "code": 0,
    "msg": "回复对话消息成功",
    "data": [question]
    "error": "",
    "time": 1762168079
}
```



