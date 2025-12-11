import os
import re
import uuid
import random
import string
from typing import List, Dict, Optional

# 生成包含字母（大小写）和数字的随机字符串
def generate_random_string(length=16):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def get_example_data() -> List[Dict[str, Optional[str]]]:
    """
    从句子提取日志中获取数据
    Content: 在苹果公司中国总部，用户和李华偶遇了从美国来的技术专家约翰·史密斯。
    Created At: 2025-11-28 19:28:38.256421
    Expired At: None
    Valid At: None
    Invalid At: None
    将数据构造成如下形式：
    [
        {
            "id":id,
            "group_id":group_id,
            "statement": Content,
            "created_at": Created At,
            "expired_at": Expired At,
            "valid_at": Valid At,
            "invalid_at": Invalid At,
            "chunk_id": "86da9022710c40eaa5f518a294c398d2",
            "entity_ids": []
        },
        ...
    ]
    """
    # 获取日志文件路径
    log_file_path = os.path.join("logs", "memory-output", "statement_extraction.txt")
    
    # 检查文件是否存在
    if not os.path.exists(log_file_path):
        return []
    
    # 读取日志文件
    with open(log_file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 解析数据
    results = []
    
    # 使用正则表达式分割每个 Statement
    statement_blocks = re.split(r"Statement \d+:", content)
    
    for block in statement_blocks[1:]:  # 跳过第一个空块
        # 提取各个字段
        id_match = re.search(r"Id:\s*(.+?)(?=\n)", block)
        group_id_match = re.search(r"Group Id:\s*(.+?)(?=\n)", block)
        statement_match = re.search(r"Content:\s*(.+?)(?=\n)", block)
        created_at_match = re.search(r"Created At:\s*(.+?)(?=\n)", block)
        expired_at_match = re.search(r"Expired At:\s*(.+?)(?=\n)", block)
        valid_at_match = re.search(r"Valid At:\s*(.+?)(?=\n)", block)
        invalid_at_match = re.search(r"Invalid At:\s*(.+?)(?=\n)", block)
        chunk_id_match = re.search(r"Chunk Id:\s*(.+?)(?=\n)", block)
        
        # 构造字典
        if statement_match:
            statement_data = {
                "id": id_match.group(1).strip() if id_match else generate_random_string(),
                "group_id": group_id_match.group(1).strip() if group_id_match else "group_example",
                "statement": statement_match.group(1).strip(),
                "created_at": created_at_match.group(1).strip() if created_at_match else None,
                "expired_at": expired_at_match.group(1).strip() if expired_at_match else None,
                "valid_at": valid_at_match.group(1).strip() if valid_at_match else None,
                "invalid_at": invalid_at_match.group(1).strip() if invalid_at_match else None,
                "chunk_id": chunk_id_match.group(1).strip() if chunk_id_match else "chunk_example",
                "entity_ids": []
            }
            
            # 将 "None" 字符串转换为 None
            for key in ["created_at", "expired_at", "valid_at", "invalid_at"]:
                if statement_data[key] == "None":
                    statement_data[key] = None
            
            results.append(statement_data)
    
    return results


if __name__ == "__main__":
    print(f"获取数据如下：\n {get_example_data()}")