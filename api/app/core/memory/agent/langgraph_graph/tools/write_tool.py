import json

from langchain_core.messages import HumanMessage, AIMessage
async def format_parsing(messages: list,type:str='string'):
    """
    格式化解析消息列表
    
    Args:
        messages: 消息列表
        type: 返回类型 ('string' 或 'dict')
        
    Returns:
        格式化后的消息列表
    """
    result = []
    user=[]
    ai=[]

    for message in messages:
        hstory_messages = message['messages']
        for history_messag in hstory_messages.strip().splitlines():
            history_messag = json.loads(history_messag)
            for content in history_messag:
                role = content['role']
                content = content['content']
                if type == "string":
                    if role == 'human' or role=="user":
                        content = '用户:' + content
                    else:
                        content = 'AI:' + content
                    result.append(content)
                if type == "dict" :
                    if role == 'human'  or role=="user":
                        user.append( content)
                    else:
                        ai.append(content)
    if type == "dict":
        for key,values in zip(user,ai):
            result.append({key:values})
    return result

async def messages_parse(messages: list | dict):
    user=[]
    ai=[]
    database=[]
    for message in messages:
        Query = message['Query']
        Query = json.loads(Query)
        for data in Query:
            role = data['role']
            if role == "human":
                user.append(data['content'])
            if role == "ai":
                ai.append(data['content'])
    for key, values in zip(user, ai):
        database.append({key, values})
    return  database


async def agent_chat_messages(user_content,ai_content):
    messages = [
        {
            "role": "user",
            "content": f"{user_content}"
        },
        {
            "role": "assistant",
            "content": f"{ai_content}"
        }

    ]
    return messages
