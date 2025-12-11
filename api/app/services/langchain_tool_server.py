import requests
import json

from dotenv import load_dotenv
import os

# 加载.env文件
load_dotenv()

# 读取web_search环境变量
web_search_value = os.getenv('web_search')
def Search(query):
    url = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
    api_key = web_search_value
    payload = json.dumps({
        "messages": [
            {
                "role": "user",
                "content": query
            }
        ], #搜索输入
        "edition":"standard", #搜索版本。默认为standard。可选值：standard：完整版本。lite：标准版本，对召回规模和精排条数简化后的版本，时延表现更好，效果略弱于完整版。
        "search_source": "baidu_search_v2", #使用的搜索引擎版本
        "resource_type_filter": [{"type": "web","top_k": 20}], #支持设置网页、视频、图片、阿拉丁搜索模态，网页top_k最大取值为50，视频top_k最大为10，图片top_k最大为30，阿拉丁top_k最大为5
        "search_filter": {
            "range": {
                "page_time": {
                    "gte": "now-1w/d", #时间查询参数，大于或等于
                    "lt": "now/d", #时间查询参数，小于
                    "gt": "", #时间查询参数，大于
                    "lte": "" #时间查询参数，小于或等于
                }
            }
        },
        "block_websites":["tieba.baidu.com"], #需要屏蔽的站点列表
        "search_recency_filter":"week", #根据网页发布时间进行筛选，可填值为：week,month,semiyear,year
        "enable_full_content":True #是否输出网页完整原文
    }, ensure_ascii=False)
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }

    response = requests.request("POST", url, headers=headers, data=payload.encode("utf-8")).json()
    content=[]
    for i in response['references']:
        title=i['title']
        snippet=i['snippet']
        content.append(title+';'+snippet)
    content='。'.join(content)
    return content