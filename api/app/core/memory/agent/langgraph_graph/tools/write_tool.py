import json

from langchain_core.messages import HumanMessage, AIMessage
async def format_parsing(messages: list,type:str='string'):
    """
    Format and parse message lists into different output types
    
    Processes message lists from storage and converts them into either string format
    or dictionary format based on the specified type parameter. Handles JSON parsing
    and role-based message organization.
    
    Args:
        messages: List of message objects from storage containing message data
        type: Return type specification ('string' for text format, 'dict' for key-value pairs)
        
    Returns:
        list: Formatted message list in the specified format
            - 'string': List of formatted text messages with role prefixes
            - 'dict': List of dictionaries mapping user messages to AI responses
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
    """
    Parse messages from storage format into user-AI conversation pairs
    
    Extracts and organizes conversation data from stored message format,
    separating user and AI messages and pairing them for database storage.
    
    Args:
        messages: List or dictionary containing stored message data with Query fields
        
    Returns:
        list: List of dictionaries containing user-AI message pairs for database storage
    """
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
    """
    Create structured chat message format for agent conversations
    
    Formats user and AI content into a standardized message structure suitable
    for agent processing and storage. Creates role-based message objects.
    
    Args:
        user_content: User's message content string
        ai_content: AI's response content string
        
    Returns:
        list: List of structured message dictionaries with role and content fields
    """
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
