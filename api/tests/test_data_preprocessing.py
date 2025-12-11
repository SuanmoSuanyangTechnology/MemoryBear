"""
数据预处理模块单元测试

测试内容：
- 数据清洗和转换
- 语义剪枝
- 数据分块（占位符）
"""

import pytest
import json
import tempfile
import os
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Mock circular import dependencies before importing
sys.modules['app.services.model_service'] = MagicMock()
sys.modules['app.repositories.model_repository'] = MagicMock()

from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_preprocessor import DataPreprocessor
from app.core.memory.models.message_models import DialogData, ConversationContext, ConversationMessage
from app.core.memory.models.config_models import PruningConfig


class TestDataPreprocessor:
    """测试数据预处理器"""
    
    def test_init_with_paths(self):
        """测试使用路径初始化"""
        preprocessor = DataPreprocessor(
            input_file_path="input.json",
            output_file_path="output.json"
        )
        assert preprocessor.input_file_path == "input.json"
        assert preprocessor.output_file_path == "output.json"
    
    def test_init_without_paths(self):
        """测试不使用路径初始化"""
        preprocessor = DataPreprocessor()
        assert preprocessor.input_file_path is not None
        assert preprocessor.output_file_path is not None
    
    def test_set_input_path(self):
        """测试设置输入路径"""
        preprocessor = DataPreprocessor()
        preprocessor.set_input_path("new_input.json")
        assert preprocessor.input_file_path == "new_input.json"
    
    def test_set_output_path(self):
        """测试设置输出路径"""
        preprocessor = DataPreprocessor()
        preprocessor.set_output_path("new_output.json")
        assert preprocessor.output_file_path == "new_output.json"
    
    def test_get_file_format(self):
        """测试获取文件格式"""
        preprocessor = DataPreprocessor()
        assert preprocessor.get_file_format("test.json") == ".json"
        assert preprocessor.get_file_format("test.csv") == ".csv"
        assert preprocessor.get_file_format("test.txt") == ".txt"
        assert preprocessor.get_file_format("test.xlsx") == ".xlsx"
    
    def test_clean_text_basic(self):
        """测试基本文本清洗"""
        preprocessor = DataPreprocessor()
        
        # 测试移除角色标识
        text = "用户: 你好"
        cleaned = preprocessor._clean_text(text)
        assert cleaned == "你好"
        
        # 测试移除URL
        text = "访问 https://example.com 了解更多"
        cleaned = preprocessor._clean_text(text)
        assert "https://example.com" not in cleaned
    
    def test_clean_text_punctuation(self):
        """测试标点符号规范化"""
        preprocessor = DataPreprocessor()
        
        # 测试感叹号转句号
        text = "太好了!!!"
        cleaned = preprocessor._clean_text(text)
        assert cleaned == "太好了。"
        
        # 测试逗号规范化
        text = "苹果,香蕉,橙子"
        cleaned = preprocessor._clean_text(text)
        assert cleaned == "苹果，香蕉，橙子"
    
    def test_clean_text_empty_input(self):
        """测试空输入"""
        preprocessor = DataPreprocessor()
        assert preprocessor._clean_text("") == ""
        assert preprocessor._clean_text(None) == ""
        assert preprocessor._clean_text("   ") == ""
    
    def test_normalize_role(self):
        """测试角色名称标准化"""
        preprocessor = DataPreprocessor()
        
        # 用户角色
        assert preprocessor._normalize_role("user") == "用户"
        assert preprocessor._normalize_role("human") == "用户"
        assert preprocessor._normalize_role("用户") == "用户"
        
        # AI角色
        assert preprocessor._normalize_role("assistant") == "AI"
        assert preprocessor._normalize_role("ai") == "AI"
        assert preprocessor._normalize_role("bot") == "AI"
        
        # 默认
        assert preprocessor._normalize_role("unknown") == "用户"
        assert preprocessor._normalize_role("") == "用户"
    
    def test_clean_data_with_content_list(self):
        """测试清洗包含content列表的数据"""
        preprocessor = DataPreprocessor()
        raw_data = [
            {
                "content": ["你好", "你好！有什么可以帮助你的吗？"],
                "group_id": "group1",
                "user_id": "user1",
                "apply_id": "app1"
            }
        ]
        
        cleaned = preprocessor.clean_data(raw_data)
        assert len(cleaned) == 1
        assert len(cleaned[0].context.msgs) == 2
        assert cleaned[0].context.msgs[0].role == "用户"
        assert cleaned[0].context.msgs[1].role == "AI"
    
    def test_clean_data_with_context_dict(self):
        """测试清洗包含context字典的数据"""
        preprocessor = DataPreprocessor()
        raw_data = [
            {
                "context": {
                    "msgs": [
                        {"role": "user", "msg": "Hello"},
                        {"role": "assistant", "msg": "Hi there!"}
                    ]
                },
                "group_id": "group1",
                "user_id": "user1",
                "apply_id": "app1"
            }
        ]
        
        cleaned = preprocessor.clean_data(raw_data)
        assert len(cleaned) == 1
        assert len(cleaned[0].context.msgs) == 2
        assert cleaned[0].context.msgs[0].role == "用户"
        assert cleaned[0].context.msgs[1].role == "AI"
    
    def test_clean_data_filters_empty_messages(self):
        """测试过滤空消息"""
        preprocessor = DataPreprocessor()
        raw_data = [
            {
                "context": {
                    "msgs": [
                        {"role": "user", "msg": "Hello"},
                        {"role": "assistant", "msg": ""},  # 空消息
                        {"role": "user", "msg": "How are you?"}
                    ]
                },
                "group_id": "group1",
                "user_id": "user1",
                "apply_id": "app1"
            }
        ]
        
        cleaned = preprocessor.clean_data(raw_data)
        assert len(cleaned) == 1
        # 空消息应该被过滤掉
        assert len(cleaned[0].context.msgs) == 2
    
    def test_clean_data_deduplicates_adjacent_messages(self):
        """测试去重相邻的重复消息"""
        preprocessor = DataPreprocessor()
        raw_data = [
            {
                "context": {
                    "msgs": [
                        {"role": "user", "msg": "Hello"},
                        {"role": "user", "msg": "Hello"},  # 重复
                        {"role": "assistant", "msg": "Hi"}
                    ]
                },
                "group_id": "group1",
                "user_id": "user1",
                "apply_id": "app1"
            }
        ]
        
        cleaned = preprocessor.clean_data(raw_data)
        assert len(cleaned) == 1
        # 重复消息应该被去重
        assert len(cleaned[0].context.msgs) == 2
    
    def test_read_json_file(self):
        """测试读取JSON文件"""
        preprocessor = DataPreprocessor()
        
        # 创建临时JSON文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            test_data = [{"test": "data"}]
            json.dump(test_data, f)
            temp_path = f.name
        
        try:
            data = preprocessor._read_json(temp_path)
            assert len(data) == 1
            assert data[0]["test"] == "data"
        finally:
            os.unlink(temp_path)
    
    def test_save_data(self):
        """测试保存数据"""
        preprocessor = DataPreprocessor()
        
        # 创建测试数据
        context = ConversationContext(msgs=[
            ConversationMessage(role="用户", msg="你好")
        ])
        dialog = DialogData(
            context=context,
            ref_id="ref1",
            group_id="group1",
            user_id="user1",
            apply_id="app1"
        )
        
        # 创建临时输出文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        
        try:
            preprocessor.save_data([dialog], temp_path)
            
            # 验证文件已创建并包含正确数据
            assert os.path.exists(temp_path)
            with open(temp_path, 'r', encoding='utf-8') as f:
                saved_data = json.load(f)
            assert len(saved_data) == 1
            assert saved_data[0]["ref_id"] == "ref1"
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


class TestSemanticPruner:
    """测试语义剪枝器"""
    
    @pytest.fixture
    def pruner_class(self):
        """动态导入 SemanticPruner 以避免循环导入"""
        from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import SemanticPruner
        return SemanticPruner
    
    def test_init_with_config(self, pruner_class):
        """测试使用配置初始化"""
        config = PruningConfig(
            pruning_switch=True,
            pruning_scene="education",
            pruning_threshold=0.5
        )
        pruner = pruner_class(config=config)
        assert pruner.config.pruning_switch is True
        assert pruner.config.pruning_scene == "education"
        assert pruner.config.pruning_threshold == 0.5
    
    def test_is_important_message_with_date(self, pruner_class):
        """测试识别包含日期的重要消息"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="会议时间是2024-11-20")
        assert pruner._is_important_message(msg) is True
    
    def test_is_important_message_with_time(self, pruner_class):
        """测试识别包含时间的重要消息"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="下午3:30见面")
        assert pruner._is_important_message(msg) is True
    
    def test_is_important_message_with_id(self, pruner_class):
        """测试识别包含编号的重要消息"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="订单号是12345")
        assert pruner._is_important_message(msg) is True
    
    def test_is_important_message_with_amount(self, pruner_class):
        """测试识别包含金额的重要消息"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="价格是100元")
        assert pruner._is_important_message(msg) is True
    
    def test_is_important_message_normal_text(self, pruner_class):
        """测试普通文本不被识别为重要消息"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="今天天气不错")
        assert pruner._is_important_message(msg) is False
    
    def test_importance_score(self, pruner_class):
        """测试重要性评分"""
        pruner = pruner_class()
        
        # 包含多个重要信息的消息应该得分更高
        msg1 = ConversationMessage(role="用户", msg="订单号12345，金额100元，2024-11-20")
        msg2 = ConversationMessage(role="用户", msg="今天天气不错")
        
        score1 = pruner._importance_score(msg1)
        score2 = pruner._importance_score(msg2)
        
        assert score1 > score2
        assert score2 == 0
    
    def test_is_filler_message(self, pruner_class):
        """测试识别填充消息"""
        pruner = pruner_class()
        
        # 常见填充语
        assert pruner._is_filler_message(ConversationMessage(role="用户", msg="你好")) is True
        assert pruner._is_filler_message(ConversationMessage(role="用户", msg="嗯嗯")) is True
        assert pruner._is_filler_message(ConversationMessage(role="用户", msg="好的")) is True
        
        # 非填充消息
        assert pruner._is_filler_message(ConversationMessage(role="用户", msg="我想了解一下产品信息")) is False
    
    def test_msg_matches_tokens(self, pruner_class):
        """测试消息是否匹配重要片段"""
        pruner = pruner_class()
        msg = ConversationMessage(role="用户", msg="订单号是12345")
        
        # 匹配
        assert pruner._msg_matches_tokens(msg, ["订单号", "12345"]) is True
        
        # 不匹配
        assert pruner._msg_matches_tokens(msg, ["发票", "地址"]) is False
        
        # 空tokens
        assert pruner._msg_matches_tokens(msg, []) is False
    
    # Note: Async tests for prune_dialog and prune_dataset are skipped
    # as they require LLM client integration which is tested in integration tests


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
