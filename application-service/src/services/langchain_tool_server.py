import requests
import json

from dotenv import load_dotenv
import os

# Load the .env file
load_dotenv()

# Read the web_search environment variable
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
        ],  # search input
        "edition": "standard",  # search edition; defaults to standard. Allowed values:
        # standard (full version), lite (simplified recall scale and ranking, better
        # latency, slightly weaker results than the full version)
        "search_source": "baidu_search_v2",  # search engine version to use
        "resource_type_filter": [{"type": "web", "top_k": 20}],
        # Supported search modalities: web, video, image, and Aladdin. Maximum top_k is
        # 50 for web, 10 for video, 30 for image, and 5 for Aladdin
        "search_filter": {
            "range": {
                "page_time": {
                    "gte": "now-1w/d",  # time query param: greater than or equal
                    "lt": "now/d",  # time query param: less than
                    "gt": "",  # time query param: greater than
                    "lte": ""  # time query param: less than or equal
                }
            }
        },
        "block_websites": ["tieba.baidu.com"],  # sites to block
        "search_recency_filter": "week",  # filter by page publish time; allowed: week,month,semiyear,year
        "enable_full_content": True  # whether to output the full page text
    }, ensure_ascii=False)
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }

    response = requests.request("POST", url, headers=headers, data=payload.encode("utf-8")).json()
    content = []
    for i in response['references']:
        title = i['title']
        snippet = i['snippet']
        content.append(title + ';' + snippet)
    content = '。'.join(content)
    return content
