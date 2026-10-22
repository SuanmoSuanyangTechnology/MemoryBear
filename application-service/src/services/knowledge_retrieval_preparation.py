"""Fail-closed stand-in for the in-process retrieval preparation pipeline.

In-process knowledge retrieval (metadata/graph/model snapshots, dvector ops)
stays served by the knowledge service; this service reaches knowledge through
the integration adapters instead. The only surface used here is the pure
common-metadata-defs intersection shared by multi-KB auto filtering.
"""

import uuid


class KnowledgeRetrievalPreparation:
    """Seam exposing only the pure helper; retrieval itself is delegated."""

    @staticmethod
    def _get_common_metadata_defs(
        metadata_defs_by_kb: dict[uuid.UUID, dict[str, dict]],
    ) -> dict[str, dict]:
        field_names = set()
        for metadata_defs in metadata_defs_by_kb.values():
            field_names.update(metadata_defs.keys())

        common_defs: dict[str, dict] = {}
        for field_name in field_names:
            common_type = None
            common_def = None
            for metadata_defs in metadata_defs_by_kb.values():
                field_def = metadata_defs.get(field_name)
                if not field_def:
                    common_def = None
                    break
                if common_type is None:
                    common_type = field_def["type"]
                    common_def = field_def
                elif common_type != field_def["type"]:
                    common_def = None
                    break
            if common_def:
                common_defs[field_name] = dict(common_def)
        return common_defs
