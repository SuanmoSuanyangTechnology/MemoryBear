"""
工作流模板加载器

从文件系统加载预定义的工作流模板
"""

import os
from pathlib import Path
from typing import Optional

import yaml

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')


class TemplateLoader:
    """工作流模板加载器"""

    def __init__(self, templates_dir: str = TEMPLATE_DIR):
        """初始化模板加载器
        
        Args:
            templates_dir: 模板目录路径
        """
        self.templates_dir = Path(templates_dir)
        if not self.templates_dir.exists():
            raise ValueError(f"模板目录不存在: {templates_dir}")

    def list_templates(self) -> list[dict]:
        """列出所有可用的模板
        
        Returns:
            模板列表，每个模板包含 id, name, description 等信息
        """
        templates = []

        # 遍历模板目录
        for template_dir in self.templates_dir.iterdir():
            if not template_dir.is_dir():
                continue

            # 检查是否有 template.yml 文件
            template_file = template_dir / "template.yml"
            if not template_file.exists():
                continue

            try:
                # 读取模板配置
                with open(template_file, 'r', encoding='utf-8') as f:
                    template_data = yaml.safe_load(f)

                # 提取模板信息
                templates.append({
                    "id": template_dir.name,
                    "name": template_data.get("name", template_dir.name),
                    "description": template_data.get("description", ""),
                    "category": template_data.get("category", "general"),
                    "tags": template_data.get("tags", []),
                    "author": template_data.get("author", ""),
                    "version": template_data.get("version", "1.0.0")
                })
            except Exception as e:
                print(f"加载模板 {template_dir.name} 失败: {e}")
                continue

        return templates

    def load_template(self, template_id: str) -> Optional[dict]:
        """加载指定的模板
        
        Args:
            template_id: 模板 ID（目录名）
        
        Returns:
            模板配置字典，如果模板不存在则返回 None
        """
        template_dir = self.templates_dir / template_id
        template_file = template_dir / "template.yml"

        if not template_file.exists():
            return None

        try:
            with open(template_file, 'r', encoding='utf-8') as f:
                template_data = yaml.safe_load(f)

            # 返回工作流配置部分
            return {
                "name": template_data.get("name", template_id),
                "description": template_data.get("description", ""),
                "nodes": template_data.get("nodes", []),
                "edges": template_data.get("edges", []),
                "variables": template_data.get("variables", []),
                "execution_config": template_data.get("execution_config", {}),
                "triggers": template_data.get("triggers", [])
            }
        except Exception as e:
            print(f"加载模板 {template_id} 失败: {e}")
            return None

    def get_template_readme(self, template_id: str) -> Optional[str]:
        """获取模板的 README 文档
        
        Args:
            template_id: 模板 ID
        
        Returns:
            README 内容，如果不存在则返回 None
        """
        template_dir = self.templates_dir / template_id
        readme_file = template_dir / "README.md"

        if not readme_file.exists():
            return None

        try:
            with open(readme_file, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print(f"读取模板 {template_id} 的 README 失败: {e}")
            return None


# 全局模板加载器实例
_template_loader: Optional[TemplateLoader] = None


def get_template_loader() -> TemplateLoader:
    """获取全局模板加载器实例
    
    Returns:
        TemplateLoader 实例
    """
    global _template_loader
    if _template_loader is None:
        _template_loader = TemplateLoader()
    return _template_loader


def list_workflow_templates() -> list[dict]:
    """列出所有工作流模板
    
    Returns:
        模板列表
    """
    loader = get_template_loader()
    return loader.list_templates()


def load_workflow_template(template_id: str) -> Optional[dict]:
    """加载工作流模板
    
    Args:
        template_id: 模板 ID
    
    Returns:
        模板配置，如果不存在则返回 None
    """
    loader = get_template_loader()
    return loader.load_template(template_id)


def get_workflow_template_readme(template_id: str) -> Optional[str]:
    """获取工作流模板的 README
    
    Args:
        template_id: 模板 ID
    
    Returns:
        README 内容，如果不存在则返回 None
    """
    loader = get_template_loader()
    return loader.get_template_readme(template_id)
