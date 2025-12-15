"""
数据预处理器 - 支持多种格式的对话数据读取、清洗和预处理

功能：
- 支持多种文件格式：JSON、CSV、Excel、TXT
- 自动检测文件编码
- 清洗和标准化对话数据
- 转换为 DialogData 对象
"""

import json
import csv
import pandas as pd
import re
import os

from typing import List, Dict, Any, Optional, Union
from pathlib import Path
from datetime import datetime

from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage


class DataPreprocessor:
    """数据预处理器类，支持多种格式的对话数据读取、清洗和预处理。"""

    def __init__(self, input_file_path: str = None, output_file_path: str = None):
        """
        初始化数据预处理器。

        Args:
            input_file_path: 输入文件路径（可选，可后续通过set_input_path设置）
            output_file_path: 输出文件路径（可选，可后续通过set_output_path设置）

        注意：您可以通过以下方式指定输入输出路径：
        1. 初始化时传入参数
        2. 调用set_input_path()和set_output_path()方法
        3. 在preprocess()方法中直接传入路径参数
        """
        self.input_file_path = input_file_path or r"src\extracted_statements.txt"
        self.output_file_path = output_file_path or r"src\data_preprocessing\out-file\extracted_statements-pre.txt"
        self.supported_formats = ['.json', '.csv', '.txt', '.xlsx', '.tsv']

    def set_input_path(self, input_path: str) -> None:
        """
        设置输入文件路径。

        Args:
            input_path: 输入文件的完整路径
        """
        self.input_file_path = input_path

    def set_output_path(self, output_path: str) -> None:
        """
        设置输出文件路径。

        Args:
            output_path: 输出文件的完整路径
        """
        self.output_file_path = output_path

    def get_file_format(self, file_path: str) -> str:
        """
        获取文件格式。

        Args:
            file_path: 文件路径

        Returns:
            文件扩展名（小写）
        """
        return Path(file_path).suffix.lower()

    def _detect_encoding(self, file_path: str) -> str:
        """
        检测文件编码，使用多种方法确保准确性。

        Args:
            file_path: 文件路径

        Returns:
            检测到的编码格式
        """
        # 常见编码列表，按优先级排序
        encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'utf-16', 'latin-1']

        # 首先尝试使用chardet检测
        try:
            import chardet
            with open(file_path, 'rb') as f:
                raw_data = f.read(10000)  # 读取前10KB进行检测
                result = chardet.detect(raw_data)
                detected_encoding = result.get('encoding')
                confidence = result.get('confidence', 0)

                # 如果检测置信度较高，使用检测结果
                if detected_encoding and confidence > 0.7:
                    return detected_encoding
        except ImportError:
            print("警告: chardet库未安装，使用备用编码检测方法")
        except Exception as e:
            print(f"chardet检测失败: {e}，使用备用方法")

        # 备用方法：尝试不同编码读取文件开头
        for encoding in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    f.read(1000)  # 尝试读取前1000个字符
                return encoding
            except (UnicodeDecodeError, UnicodeError):
                continue

        # 如果所有编码都失败，返回utf-8作为最后选择
        return 'utf-8'

    def _read_json(self, data_path: str) -> List[Dict[str, Any]]:
        """
        读取JSON格式的对话数据，支持标准JSON和JSONL格式。

        Args:
            data_path: JSON文件路径

        Returns:
            解析后的数据列表
        """
        encoding = self._detect_encoding(data_path)
        content = None

        # 尝试使用检测到的编码读取文件
        encodings_to_try = [encoding, 'utf-8', 'gbk', 'gb2312', 'latin-1']

        for enc in encodings_to_try:
            try:
                with open(data_path, 'r', encoding=enc) as f:
                    content = f.read().strip()
                print(f"成功使用编码 {enc} 读取文件")
                break
            except (UnicodeDecodeError, UnicodeError) as e:
                print(f"编码 {enc} 读取失败: {e}")
                continue

        if content is None:
            raise ValueError(f"无法使用任何编码读取文件: {data_path}")

        try:

            # 尝试解析为标准JSON
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    return [data]
                elif isinstance(data, list):
                    return data
                else:
                    raise ValueError(f"不支持的JSON数据结构: {type(data)}")
            except json.JSONDecodeError as e:
                # 如果标准JSON解析失败，尝试JSONL格式（每行一个JSON对象）
                print(f"标准JSON解析失败: {e}，尝试JSONL格式...")
                data_list = []
                lines = content.split('\n')

                for line_num, line in enumerate(lines, 1):
                    line = line.strip()
                    if line:  # 跳过空行
                        try:
                            json_obj = json.loads(line)
                            data_list.append(json_obj)
                        except json.JSONDecodeError as line_error:
                            # 如果是单行巨大JSON数组，可能需要特殊处理
                            if line_num == 1 and len(lines) == 1:
                                print("检测到单行大型JSON，尝试分块解析...")
                                # 对于超大单行JSON，尝试使用json.JSONDecoder进行流式解析
                                try:
                                    decoder = json.JSONDecoder()
                                    idx = 0
                                    while idx < len(line):
                                        line = line[idx:].lstrip()
                                        if not line:
                                            break
                                        try:
                                            obj, end_idx = decoder.raw_decode(line)
                                            if isinstance(obj, list):
                                                data_list.extend(obj)
                                            elif isinstance(obj, dict):
                                                data_list.append(obj)
                                            idx += end_idx
                                        except json.JSONDecodeError:
                                            break
                                except Exception as decode_error:
                                    print(f"分块解析也失败: {decode_error}")
                            else:
                                print(f"警告: 第{line_num}行JSON解析失败: {line_error}")
                            continue

                return data_list

        except Exception as e:
            raise ValueError(f"读取JSON文件时发生错误: {e}")

    def _read_csv(self, data_path: str) -> List[Dict[str, Any]]:
        """
        读取CSV格式的对话数据。

        Args:
            data_path: CSV文件路径

        Returns:
            解析后的数据列表
        """
        encoding = self._detect_encoding(data_path)
        encodings_to_try = [encoding, 'utf-8', 'gbk', 'gb2312', 'latin-1']

        for enc in encodings_to_try:
            try:
                # 尝试不同的分隔符
                separators = [',', '\t', ';', '|']
                df = None

                for sep in separators:
                    try:
                        df = pd.read_csv(data_path, encoding=enc, sep=sep)
                        if len(df.columns) > 1:  # 如果成功分割出多列，则认为找到了正确的分隔符
                            break
                    except Exception:
                        continue

                if df is None:
                    df = pd.read_csv(data_path, encoding=enc)

                print(f"成功使用编码 {enc} 读取CSV文件")
                return df.to_dict('records')

            except (UnicodeDecodeError, UnicodeError) as e:
                print(f"编码 {enc} 读取CSV失败: {e}")
                continue
            except Exception as e:
                if enc == encodings_to_try[-1]:  # 最后一个编码也失败了
                    raise ValueError(f"读取CSV文件失败: {e}")
                continue

        raise ValueError(f"无法使用任何编码读取CSV文件: {data_path}")

    def _read_excel(self, data_path: str) -> List[Dict[str, Any]]:
        """
        读取Excel格式的对话数据。

        Args:
            data_path: Excel文件路径

        Returns:
            解析后的数据列表
        """
        try:
            df = pd.read_excel(data_path)
            return df.to_dict('records')
        except Exception as e:
            raise ValueError(f"读取Excel文件失败: {e}")

    def _read_text(self, data_path: str) -> List[Dict[str, Any]]:
        """
        读取纯文本格式的对话数据。

        Args:
            data_path: 文本文件路径

        Returns:
            解析后的数据列表
        """
        encoding = self._detect_encoding(data_path)
        encodings_to_try = [encoding, 'utf-8', 'gbk', 'gb2312', 'latin-1']
        content = None

        # 尝试使用不同编码读取文件
        for enc in encodings_to_try:
            try:
                with open(data_path, 'r', encoding=enc) as f:
                    content = f.read()
                print(f"成功使用编码 {enc} 读取文本文件")
                break
            except (UnicodeDecodeError, UnicodeError) as e:
                print(f"编码 {enc} 读取文本失败: {e}")
                continue

        if content is None:
            raise ValueError(f"无法使用任何编码读取文本文件: {data_path}")

        try:

            # 尝试解析不同的文本格式
            lines = content.strip().split('\n')

            # 格式1: 每行一个对话轮次，格式为 "角色: 内容" 或 "角色：内容"
            messages = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 尝试匹配 "角色: 内容" 或 "角色：内容" 格式
                match = re.match(r'^([^:：]+)[：:]\s*(.+)$', line)
                if match:
                    role, msg = match.groups()
                    messages.append({'role': role.strip(), 'msg': msg.strip()})
                else:
                    # 如果不匹配，则作为用户消息处理
                    messages.append({'role': 'User', 'msg': line})

            if messages:
                return [{'context': {'msgs': messages}}]
            else:
                # 如果没有解析出消息，则将整个文本作为一条消息
                return [{'context': {'msgs': [{'role': 'User', 'msg': content}]}}]

        except Exception as e:
            raise ValueError(f"读取文本文件失败: {e}")

    def read_data(self, data_path: str = None) -> List[Dict[str, Any]]:
        """
        根据文件格式自动选择合适的读取方法。

        Args:
            data_path: 数据文件路径（如果为None，则使用初始化时设置的路径）

        Returns:
            解析后的原始数据列表
        """
        if data_path is None:
            data_path = self.input_file_path

        if not data_path:
            raise ValueError("请指定输入文件路径")

        if not os.path.exists(data_path):
            raise FileNotFoundError(f"文件不存在: {data_path}")

        file_format = self.get_file_format(data_path)

        if file_format == '.json':
            return self._read_json(data_path)
        elif file_format == '.csv':
            return self._read_csv(data_path)
        elif file_format in ['.xlsx', '.xls']:
            return self._read_excel(data_path)
        elif file_format in ['.txt', '.tsv']:
            return self._read_text(data_path)
        else:
            raise ValueError(f"不支持的文件格式: {file_format}。支持的格式: {self.supported_formats}")

    def _clean_text(self, text: str) -> str:
        """
        增强的文本清洗函数。
        """
        if not text or not isinstance(text, str):
            return ""

        # 1. 移除消息中的角色标识（支持英文冒号":"与中文冒号"："）
        text = re.sub(r'^(用户|AI|user|ai|assistant|bot|助手|机器人)[：:]\s*', '', text, flags=re.IGNORECASE)

        # 2. 移除URL链接
        text = re.sub(r'https?://[^\s]+', '', text)
        text = re.sub(r'www\.[^\s]+', '', text)

        # 3. 移除HTML标签
        text = re.sub(r'<[^>]+>', '', text)

        # 4. 移除乱码和控制字符
        text = re.sub(r'[�]+', '', text)
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)

        # 5. 标点符号规范化
        # 将连续的感叹号（中英文）替换为一个句号
        text = re.sub(r'[!！]+', '。', text)
        # 将连续的句点/省略号（中英文）替换为一个句号
        text = re.sub(r'(…{1,}|\.{2,}|。{2,})', '。', text)
        # 将英文句点统一为中文句号（避免残留英文句点影响断句）
        text = re.sub(r'\.', '。', text)
        # 将连续的逗号（中英文）规范为一个中文逗号
        text = re.sub(r'[，,]{2,}', '，', text)
        # 将英文逗号统一为中文逗号
        text = re.sub(r',', '，', text)

        # 6. 规范化空白字符
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        return text

    def _parse_message_content(self, content: str) -> List[Dict[str, str]]:
        """
        增强的消息内容解析。
        """
        messages = []

        # 先清洗内容
        cleaned_content = self._clean_text(content)

        if not cleaned_content:
            return messages

        # 检查是否为有效消息（至少包含中文或英文单词）
        if not re.search(r'[\u4e00-\u9fff\w]', cleaned_content):
            return messages

        # 根据内容特征判断角色（更智能的角色识别）
        if re.search(r'(你好|嗨|早上好|晚上好|请问|谢谢|抱歉)', cleaned_content):
            role = 'User'
        elif re.search(r'(很高兴|建议|推荐|可以帮助|请提供)', cleaned_content):
            role = 'Assistant'
        else:
            role = 'User'  # 默认

        messages.append({'role': role, 'msg': cleaned_content})

        return messages

    def _filter_empty_messages(self, messages: List[ConversationMessage]) -> List[ConversationMessage]:
        """
        更严格的空消息过滤。
        """
        filtered = []
        for msg in messages:
            # 检查消息是否有效
            if (msg.msg and
                isinstance(msg.msg, str) and
                len(msg.msg.strip()) >= 2 and  # 至少2个字符
                re.search(r'[\u4e00-\u9fff\w]', msg.msg)):  # 包含有效字符
                filtered.append(msg)
        return filtered


    def _normalize_role(self, role: str) -> str:
        """
        标准化角色名称。

        Args:
            role: 原始角色名称

        Returns:
            标准化后的角色名称
        """
        if not role or not isinstance(role, str):
            return "User"

        role = role.strip().lower()

        # 用户角色的各种表示
        user_roles = ['user', 'human', '用户', '人类', 'customer', '客户', 'u']
        # AI角色的各种表示
        ai_roles = ['assistant', 'ai', 'bot', 'chatbot', '助手', '机器人', 'system', 'a']

        if role in user_roles:
            return "User"
        elif role in ai_roles:
            return "Assistant"
        else:
            return "User"  # 默认为用户

    def clean_data(self, raw_data: List[Dict[str, Any]], skip_cleaning: bool = True) -> List[DialogData]:
        """
        清洗原始数据并转换为DialogData对象。

        Args:
            raw_data: 原始数据列表
            skip_cleaning: 是否跳过数据清洗，直接转换为DialogData对象（默认False）

        Returns:
            清洗后的DialogData对象列表
        """
        if skip_cleaning:
            print("跳过数据清洗步骤，直接转换数据...")
            return self._convert_to_dialog_data(raw_data)
        
        cleaned_dialogs = []

        for i, item in enumerate(raw_data):
            conv_date: Optional[str] = None
            try:
                # 提取对话消息
                messages = []

                # 处理不同的数据结构
                if 'content' in item and isinstance(item['content'], list):
                    # 新格式：dialog_release_zh.json格式，content是字符串数组
                    content_list = item['content']
                    for j, content_text in enumerate(content_list):
                        # 交替分配角色：偶数索引为用户，奇数索引为AI
                        role = 'User' if j % 2 == 0 else 'Assistant'
                        normalized_role = self._normalize_role(role)

                        # 清洗消息内容
                        cleaned_content = self._clean_text(str(content_text))

                        # 过滤空消息
                        if cleaned_content:
                            messages.append(ConversationMessage(role=normalized_role, msg=cleaned_content))

                elif 'context' in item and isinstance(item['context'], dict) and 'msgs' in item['context']:
                    # 标准格式：context是字典且包含msgs
                    raw_messages = item['context']['msgs']
                elif 'context' in item and isinstance(item['context'], str):
                    # testdata.json格式：context是字符串，需要解析对话内容
                    context_text = item['context']
                    # 从context文本中解析绝对日期并存入conv_date（格式：YYYY-MM-DD）
                    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", context_text)
                    if m:
                        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        conv_date = f"{y:04d}-{mo:02d}-{d:02d}"
                    else:
                        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", context_text)
                        if m:
                            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                            conv_date = f"{y:04d}-{mo:02d}-{d:02d}"
                    messages = self._parse_context_string(context_text)
                elif 'messages' in item:
                    # 另一种常见格式
                    raw_messages = item['messages']
                elif 'conversation' in item:
                    # 对话格式
                    raw_messages = item['conversation']
                else:
                    # 尝试直接解析
                    raw_messages = [item] if 'role' in item and 'msg' in item else []

                # 如果messages还是空的，说明需要处理raw_messages
                if not messages and 'raw_messages' in locals():
                    # 清洗每条消息
                    for msg_data in raw_messages:
                        if isinstance(msg_data, dict):
                            role = self._normalize_role(msg_data.get('role', 'User'))
                            content = msg_data.get('msg', msg_data.get('content', msg_data.get('message', '')))

                            # 清洗消息内容
                            cleaned_content = self._clean_text(str(content))

                            # 过滤空消息
                            if cleaned_content:
                                messages.append(ConversationMessage(role=role, msg=cleaned_content))

                # 过滤空对话
                if not messages:
                    continue

                # 去重相邻的重复消息
                deduplicated_messages = []
                for msg in messages:
                    if not deduplicated_messages or (
                        deduplicated_messages[-1].role != msg.role or
                        deduplicated_messages[-1].msg != msg.msg
                    ):
                        deduplicated_messages.append(msg)

                # 创建DialogData对象
                context = ConversationContext(msgs=deduplicated_messages)
                # 获取对话ID，优先使用dialog_id，然后是ref_id、id，最后生成默认ID
                dialog_id = item.get('dialog_id', item.get('ref_id', item.get('id', f'dialog_{i}')))


                # 获取group_id，如果不存在则生成默认值
                group_id = item.get('group_id', f'group_default_{i}')
                user_id = item.get('user_id', f'user_default_{i}')
                apply_id = item.get('apply_id', f'apply_default_{i}')


                # 构建元数据，附加解析到的会话日期
                metadata = {
                    **item.get('metadata', {}),
                    'document_id': str(item.get('document_id', 'unknown')) if item.get('document_id') is not None else 'unknown',
                    'original_format': 'dialog_release_zh' if 'content' in item and isinstance(item['content'], list) else 'testdata'
                }
                if conv_date:
                    metadata['conversation_date'] = conv_date
                    metadata['publication_date'] = conv_date

                dialog_data = DialogData(
                    context=context,
                    ref_id=dialog_id,
                    group_id=group_id,
                    user_id=user_id,
                    apply_id=apply_id,
                    metadata=metadata
                )

                cleaned_dialogs.append(dialog_data)

            except Exception as e:
                print(f"警告: 处理第{i+1}条数据时出错: {e}")
                continue

        return cleaned_dialogs

    def _convert_to_dialog_data(self, raw_data: List[Dict[str, Any]]) -> List[DialogData]:
        """
        直接将原始数据转换为DialogData对象，不进行清洗。

        Args:
            raw_data: 原始数据列表

        Returns:
            DialogData对象列表
        """
        dialog_list = []
        
        for i, item in enumerate(raw_data):
            try:
                messages = []
                
                # 处理不同的数据结构
                if 'content' in item and isinstance(item['content'], list):
                    content_list = item['content']
                    for j, content_text in enumerate(content_list):
                        role = 'User' if j % 2 == 0 else 'Assistant'
                        if content_text:
                            messages.append(ConversationMessage(role=role, msg=str(content_text)))
                
                elif 'context' in item and isinstance(item['context'], dict) and 'msgs' in item['context']:
                    raw_messages = item['context']['msgs']
                    for msg_data in raw_messages:
                        if isinstance(msg_data, dict):
                            role = msg_data.get('role', 'User')
                            content = msg_data.get('msg', msg_data.get('content', msg_data.get('message', '')))
                            if content:
                                messages.append(ConversationMessage(role=role, msg=str(content)))
                
                elif 'context' in item and isinstance(item['context'], str):
                    # 尝试解析结构化对话，如果失败则作为单条用户消息处理
                    messages = self._parse_context_string(item['context'])
                    if not messages:
                        # 如果没有解析出结构化消息，将整个context作为用户消息
                        context_text = item['context'].strip()
                        if context_text:
                            messages.append(ConversationMessage(role='User', msg=context_text))
                
                elif 'messages' in item:
                    raw_messages = item['messages']
                    for msg_data in raw_messages:
                        if isinstance(msg_data, dict):
                            role = msg_data.get('role', 'User')
                            content = msg_data.get('msg', msg_data.get('content', msg_data.get('message', '')))
                            if content:
                                messages.append(ConversationMessage(role=role, msg=str(content)))
                
                if not messages:
                    continue
                
                context = ConversationContext(msgs=messages)
                dialog_id = item.get('dialog_id', item.get('ref_id', item.get('id', f'dialog_{i}')))
                group_id = item.get('group_id', f'group_default_{i}')
                user_id = item.get('user_id', f'user_default_{i}')
                apply_id = item.get('apply_id', f'apply_default_{i}')
                
                metadata = {
                    **item.get('metadata', {}),
                    'document_id': str(item.get('document_id', 'unknown')) if item.get('document_id') is not None else 'unknown',
                    'original_format': 'raw'
                }
                
                dialog_data = DialogData(
                    context=context,
                    ref_id=dialog_id,
                    group_id=group_id,
                    user_id=user_id,
                    apply_id=apply_id,
                    metadata=metadata
                )
                
                dialog_list.append(dialog_data)
                
            except Exception as e:
                print(f"警告: 转换第{i+1}条数据时出错: {e}")
                continue
        
        return dialog_list

    def _parse_context_string(self, context_text: str) -> List[ConversationMessage]:
        """
        解析context字符串中的对话内容。

        Args:
            context_text: 包含对话的字符串

        Returns:
            解析后的ConversationMessage列表
        """
        messages = []

        # 使用正则表达式匹配对话模式
        # 匹配 "User: 内容" / "用户: 内容" 或 "Assistant: 内容" / "AI: 内容" 格式
        pattern = r'(User|用户|Assistant|AI|user|assistant)[：:]\s*([^\n]+(?:\n(?!(?:User|用户|Assistant|AI|user|assistant)[：:])[^\n]*)*?)'
        matches = re.findall(pattern, context_text, re.MULTILINE | re.DOTALL | re.IGNORECASE)

        for role, content in matches:
            # 标准化角色名称
            normalized_role = self._normalize_role(role)

            # 清洗消息内容
            cleaned_content = self._clean_text(content.strip())

            # 过滤空消息
            if cleaned_content:
                messages.append(ConversationMessage(role=normalized_role, msg=cleaned_content))

        return messages

    def save_data(self, dialog_data_list: List[DialogData], output_path: str = None) -> None:
        """
        保存处理后的数据。

        Args:
            dialog_data_list: DialogData对象列表
            output_path: 输出文件路径（如果为None，则使用初始化时设置的路径）
        """
        if output_path is None:
            output_path = self.output_file_path

        if not output_path:
            raise ValueError("请指定输出文件路径")

        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 转换为可序列化的格式
        serializable_data = []
        for dialog in dialog_data_list:
            serializable_data.append({
                'id': dialog.id,
                'ref_id': dialog.ref_id,
                'created_at': dialog.created_at.isoformat(),
                'context': {
                    'msgs': [{'role': msg.role, 'msg': msg.msg} for msg in dialog.context.msgs]
                },
                'metadata': dialog.metadata,
                'chunks': []
            })

        # 保存为JSON格式
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_data, f, ensure_ascii=False, indent=2)

        print(f"数据已保存到: {output_path}")

    def preprocess(self, input_path: str = None, output_path: str = None, skip_cleaning: bool = True, indices: Optional[List[int]] = None) -> List[DialogData]:
        """
        完整的数据预处理流程。

        Args:
            input_path: 输入文件路径（可选）
            output_path: 输出文件路径（可选）
            skip_cleaning: 是否跳过数据清洗步骤（默认False）
            indices: 要处理的数据索引列表（可选）

        Returns:
            处理后的DialogData对象列表
        """
        print("开始数据预处理...")

        # 读取原始数据
        print("正在读取数据...")
        raw_data = self.read_data(input_path)
        print(f"成功读取 {len(raw_data)} 条原始数据")
        
        # 根据索引筛选数据
        if indices:
            selected = [raw_data[i] for i in indices if 0 <= i < len(raw_data)]
            if selected:
                raw_data = selected
                print(f"根据索引 {indices} 筛选后，保留 {len(raw_data)} 条数据")
            else:
                print(f"警告: 提供的索引 {indices} 筛选为空，处理全部 {len(raw_data)} 条数据")

        # 清洗数据
        if skip_cleaning:
            print("跳过数据清洗步骤...")
            cleaned_data = self.clean_data(raw_data, skip_cleaning=True)
        else:
            print("正在清洗数据...")
            cleaned_data = self.clean_data(raw_data, skip_cleaning=False)
        print(f"处理完成，得到 {len(cleaned_data)} 条有效对话")

        # 保存数据（如果指定了输出路径）
        if output_path or self.output_file_path:
            print("正在保存数据...")
            self.save_data(cleaned_data, output_path)

        print("数据预处理完成！")
        return cleaned_data
