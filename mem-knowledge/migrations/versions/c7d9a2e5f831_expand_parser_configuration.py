# ruff: noqa: E501
"""Expand typed parser configuration storage. Frozen DDL; no data rewrite.

The core source/strategy revision must be installed before the backfill CLI.
"""

from alembic import op

revision = "c7d9a2e5f831"
down_revision = "72e42a8c6d19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE knowledge_parser_configs (
	knowledge_id UUID NOT NULL, 
	layout_recognize TEXT NOT NULL, 
	html4excel BOOLEAN NOT NULL, 
	analyze_hyperlink BOOLEAN NOT NULL, 
	markdown_hyperlink_enabled BOOLEAN NOT NULL, 
	image_vision_enabled BOOLEAN NOT NULL, 
	image_vision_mode SMALLINT NOT NULL, 
	embedded_image_vision_enabled BOOLEAN NOT NULL, 
	chunk_method TEXT, 
	chunk_token_num INTEGER NOT NULL, 
	chunk_overlap INTEGER NOT NULL, 
	delimiters TEXT[] NOT NULL, 
	auto_keywords INTEGER NOT NULL, 
	parent_chunk_mode TEXT NOT NULL, 
	parent_chunk_token_num INTEGER NOT NULL, 
	parent_chunk_delimiters TEXT[] NOT NULL, 
	auto_questions INTEGER, 
	qa_prompt TEXT, 
	multimodal_enabled BOOLEAN NOT NULL, 
	graph_enabled BOOLEAN NOT NULL, 
	graph_scene_name TEXT NOT NULL, 
	graph_entity_types TEXT[] NOT NULL, 
	graph_method TEXT NOT NULL, 
	graph_resolution BOOLEAN NOT NULL, 
	graph_community BOOLEAN NOT NULL, 
	graph_pipeline TEXT NOT NULL, 
	wiki_language TEXT NOT NULL, 
	wiki_extract_prompt TEXT NOT NULL, 
	web_entry_url TEXT, 
	web_max_pages INTEGER, 
	web_delay_seconds FLOAT, 
	web_timeout_seconds FLOAT, 
	web_user_agent TEXT, 
	third_party_platform TEXT, 
	yuque_user_id TEXT, 
	yuque_token TEXT, 
	feishu_app_id TEXT, 
	feishu_app_secret TEXT, 
	feishu_folder_token TEXT, 
	extras JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('utc', now()) NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('utc', now()) NOT NULL, 
	PRIMARY KEY (knowledge_id), 
	CONSTRAINT ck_kpc_layout_recognize CHECK (layout_recognize IN ('plain', 'mineru', 'textln')), 
	CONSTRAINT ck_kpc_image_vision_mode CHECK (image_vision_mode IN (0, 1, 2)), 
	CONSTRAINT ck_kpc_chunk_method CHECK (chunk_method IN ('default', 'custom', 'parent_child')), 
	CONSTRAINT ck_kpc_parent_chunk_mode CHECK (parent_chunk_mode IN ('paragraph', 'full-doc')), 
	CONSTRAINT ck_kpc_graph_pipeline CHECK (graph_pipeline IN ('legacy', 'evidence')), 
	CONSTRAINT ck_kpc_third_party_platform CHECK (third_party_platform IN ('yuque', 'feishu')), 
	CONSTRAINT ck_kpc_chunk_token_num_positive CHECK (chunk_token_num > 0), 
	CONSTRAINT ck_kpc_chunk_overlap_nonnegative CHECK (chunk_overlap >= 0), 
	CONSTRAINT ck_kpc_auto_keywords_nonnegative CHECK (auto_keywords >= 0), 
	CONSTRAINT ck_kpc_parent_tokens_positive CHECK (parent_chunk_token_num > 0), 
	CONSTRAINT ck_kpc_auto_questions_range CHECK (auto_questions BETWEEN 0 AND 10), 
	CONSTRAINT ck_kpc_parent_child_qa CHECK (chunk_method IS DISTINCT FROM 'parent_child' OR auto_questions = 0), 
	CONSTRAINT ck_kpc_extras CHECK (jsonb_typeof(extras) = 'object'), 
	CONSTRAINT ck_kpc_chunk_method_fields CHECK (chunk_method IS NULL OR auto_questions IS NOT NULL), 
	CONSTRAINT ck_kpc_wiki_language CHECK (char_length(wiki_language) BETWEEN 1 AND 64), 
	CONSTRAINT ck_kpc_wiki_prompt CHECK (char_length(wiki_extract_prompt) <= 12000), 
	CONSTRAINT ck_kpc_yuque_fields CHECK (third_party_platform IS DISTINCT FROM 'yuque' OR (yuque_user_id IS NOT NULL AND yuque_token IS NOT NULL)), 
	CONSTRAINT ck_kpc_feishu_fields CHECK (third_party_platform IS DISTINCT FROM 'feishu' OR (feishu_app_id IS NOT NULL AND feishu_app_secret IS NOT NULL AND feishu_folder_token IS NOT NULL)), 
	CONSTRAINT ck_kpc_other_platform_cleared CHECK ((third_party_platform IS NOT DISTINCT FROM 'yuque' OR (yuque_user_id IS NULL AND yuque_token IS NULL)) AND (third_party_platform IS NOT DISTINCT FROM 'feishu' OR (feishu_app_id IS NULL AND feishu_app_secret IS NULL AND feishu_folder_token IS NULL))), 
	CONSTRAINT ck_kpc_web_values CHECK ((web_max_pages IS NULL OR web_max_pages >= 1) AND (web_delay_seconds IS NULL OR web_delay_seconds >= 0) AND (web_timeout_seconds IS NULL OR web_timeout_seconds > 0)), 
	CONSTRAINT fk_knowledge_parser_configs_owner FOREIGN KEY(knowledge_id) REFERENCES knowledges (id) ON DELETE CASCADE
)
""")
    op.execute("COMMENT ON TABLE knowledge_parser_configs IS '所属对象的一对一解析配置'")
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.knowledge_id IS '所属对象 ID；物理删除级联，软删除保留'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.layout_recognize IS '解析配置 defaults.parsing.layout'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.html4excel IS '解析配置 defaults.parsing.html4excel'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.analyze_hyperlink IS '解析配置 defaults.parsing.follow_hyperlinks'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.markdown_hyperlink_enabled IS '解析配置 defaults.parsing.extract_markdown_hyperlinks'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.image_vision_enabled IS '解析配置 defaults.image.enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.image_vision_mode IS '解析配置 defaults.image.mode'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.embedded_image_vision_enabled IS '解析配置 defaults.image.embedded_enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.chunk_method IS '解析配置 defaults.chunking.method'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.chunk_token_num IS '解析配置 defaults.chunking.max_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.chunk_overlap IS '解析配置 defaults.chunking.overlap_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.delimiters IS '解析配置 defaults.chunking.delimiters'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.auto_keywords IS '解析配置 defaults.chunking.auto_keywords'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.parent_chunk_mode IS '解析配置 defaults.chunking.parent.mode'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.parent_chunk_token_num IS '解析配置 defaults.chunking.parent.max_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.parent_chunk_delimiters IS '解析配置 defaults.chunking.parent.delimiters'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.auto_questions IS '解析配置 defaults.qa.questions_per_chunk'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.qa_prompt IS '解析配置 defaults.qa.prompt'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.multimodal_enabled IS '解析配置 multimodal.enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_enabled IS '解析配置 graph.enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_scene_name IS '解析配置 graph.scene_name'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_entity_types IS '解析配置 graph.entity_types'"
    )
    op.execute("COMMENT ON COLUMN knowledge_parser_configs.graph_method IS '解析配置 graph.method'")
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_resolution IS '解析配置 graph.resolution'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_community IS '解析配置 graph.community'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.graph_pipeline IS '解析配置 graph.pipeline'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.wiki_language IS '解析配置 wiki.language'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.wiki_extract_prompt IS '解析配置 wiki.extract_prompt'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.web_entry_url IS '解析配置 sources.website.entry_url'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.web_max_pages IS '解析配置 sources.website.max_pages'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.web_delay_seconds IS '解析配置 sources.website.delay_seconds'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.web_timeout_seconds IS '解析配置 sources.website.timeout_seconds'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.web_user_agent IS '解析配置 sources.website.user_agent'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.third_party_platform IS '解析配置 sources.third_party.platform'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.yuque_user_id IS '解析配置 sources.third_party.yuque.user_id'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.yuque_token IS '解析配置 sources.third_party.yuque.token'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.feishu_app_id IS '解析配置 sources.third_party.feishu.app_id'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.feishu_app_secret IS '解析配置 sources.third_party.feishu.app_secret'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.feishu_folder_token IS '解析配置 sources.third_party.feishu.folder_token'"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_parser_configs.extras IS '未登记历史键；只供旧响应回显'"
    )
    op.execute("COMMENT ON COLUMN knowledge_parser_configs.created_at IS '创建时间 UTC'")
    op.execute("COMMENT ON COLUMN knowledge_parser_configs.updated_at IS '修改时间 UTC'")
    op.execute("""
CREATE TABLE document_parser_configs (
	document_id UUID NOT NULL, 
	layout_recognize TEXT NOT NULL, 
	html4excel BOOLEAN NOT NULL, 
	analyze_hyperlink BOOLEAN NOT NULL, 
	markdown_hyperlink_enabled BOOLEAN NOT NULL, 
	image_vision_enabled BOOLEAN NOT NULL, 
	image_vision_mode SMALLINT NOT NULL, 
	embedded_image_vision_enabled BOOLEAN NOT NULL, 
	chunk_method TEXT NOT NULL, 
	chunk_token_num INTEGER NOT NULL, 
	chunk_overlap INTEGER NOT NULL, 
	delimiters TEXT[] NOT NULL, 
	auto_keywords INTEGER NOT NULL, 
	parent_chunk_mode TEXT NOT NULL, 
	parent_chunk_token_num INTEGER NOT NULL, 
	parent_chunk_delimiters TEXT[] NOT NULL, 
	auto_questions INTEGER NOT NULL, 
	qa_prompt TEXT, 
	doc_type TEXT, 
	multimodal_enabled BOOLEAN NOT NULL, 
	vision_model_id UUID, 
	audio_model_id UUID, 
	video_model_id UUID, 
	extras JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('utc', now()) NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT timezone('utc', now()) NOT NULL, 
	PRIMARY KEY (document_id), 
	CONSTRAINT ck_dpc_layout_recognize CHECK (layout_recognize IN ('plain', 'mineru', 'textln')), 
	CONSTRAINT ck_dpc_image_vision_mode CHECK (image_vision_mode IN (0, 1, 2)), 
	CONSTRAINT ck_dpc_chunk_method CHECK (chunk_method IN ('default', 'custom', 'parent_child')), 
	CONSTRAINT ck_dpc_parent_chunk_mode CHECK (parent_chunk_mode IN ('paragraph', 'full-doc')), 
	CONSTRAINT ck_dpc_doc_type CHECK (doc_type IN ('qa')), 
	CONSTRAINT ck_dpc_chunk_token_num_positive CHECK (chunk_token_num > 0), 
	CONSTRAINT ck_dpc_chunk_overlap_nonnegative CHECK (chunk_overlap >= 0), 
	CONSTRAINT ck_dpc_auto_keywords_nonnegative CHECK (auto_keywords >= 0), 
	CONSTRAINT ck_dpc_parent_tokens_positive CHECK (parent_chunk_token_num > 0), 
	CONSTRAINT ck_dpc_auto_questions_range CHECK (auto_questions BETWEEN 0 AND 10), 
	CONSTRAINT ck_dpc_parent_child_qa CHECK (chunk_method IS DISTINCT FROM 'parent_child' OR auto_questions = 0), 
	CONSTRAINT ck_dpc_extras CHECK (jsonb_typeof(extras) = 'object'), 
	CONSTRAINT fk_document_parser_configs_owner FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
)
""")
    op.execute("COMMENT ON TABLE document_parser_configs IS '所属对象的一对一解析配置'")
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.document_id IS '所属对象 ID；物理删除级联，软删除保留'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.layout_recognize IS '解析配置 parsing.layout'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.html4excel IS '解析配置 parsing.html4excel'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.analyze_hyperlink IS '解析配置 parsing.follow_hyperlinks'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.markdown_hyperlink_enabled IS '解析配置 parsing.extract_markdown_hyperlinks'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.image_vision_enabled IS '解析配置 image.enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.image_vision_mode IS '解析配置 image.mode'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.embedded_image_vision_enabled IS '解析配置 image.embedded_enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.chunk_method IS '解析配置 chunking.method'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.chunk_token_num IS '解析配置 chunking.max_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.chunk_overlap IS '解析配置 chunking.overlap_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.delimiters IS '解析配置 chunking.delimiters'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.auto_keywords IS '解析配置 chunking.auto_keywords'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.parent_chunk_mode IS '解析配置 chunking.parent.mode'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.parent_chunk_token_num IS '解析配置 chunking.parent.max_tokens'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.parent_chunk_delimiters IS '解析配置 chunking.parent.delimiters'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.auto_questions IS '解析配置 qa.questions_per_chunk'"
    )
    op.execute("COMMENT ON COLUMN document_parser_configs.qa_prompt IS '解析配置 qa.prompt'")
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.doc_type IS '解析配置 parsing.document_kind'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.multimodal_enabled IS '解析配置 multimodal.enabled'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.vision_model_id IS '解析配置 multimodal.vision_model_id'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.audio_model_id IS '解析配置 multimodal.audio_model_id'"
    )
    op.execute(
        "COMMENT ON COLUMN document_parser_configs.video_model_id IS '解析配置 multimodal.video_model_id'"
    )
    op.execute("COMMENT ON COLUMN document_parser_configs.extras IS '未登记历史键；只供旧响应回显'")
    op.execute("COMMENT ON COLUMN document_parser_configs.created_at IS '创建时间 UTC'")
    op.execute("COMMENT ON COLUMN document_parser_configs.updated_at IS '修改时间 UTC'")


def downgrade() -> None:
    op.drop_table("document_parser_configs")
    op.drop_table("knowledge_parser_configs")
