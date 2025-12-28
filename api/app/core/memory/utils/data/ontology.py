from enum import StrEnum


# Use jinja template.render
PREDICATE_DEFINITIONS = {
    # Core Relationships
    "IS_A": "Denotes a class-or-type relationship between two entities (e.g., 'Model Y IS_A electric-SUV'). Includes 'is' and 'was'.",
    "HAS_A": "Denotes a part-whole relationship between two entities (e.g., 'Model Y HAS_A electric-engine'). Includes 'has' and 'had'.",
    "LOCATED_IN": "Specifies geographic or organisational containment or proximity (e.g., headquarters LOCATED_IN Berlin).",
    
    # Business/Corporate
    "HOLDS_ROLE": "Connects a person to a formal office or title within an organisation (CEO, Chair, Director, etc.).",
    "PRODUCES": "Indicates that an entity manufactures, builds, or creates a product, service, or infrastructure (includes scale-ups and component inclusion).",
    "SELLS": "Marks a commercial seller-to-customer relationship for a product or service (markets, distributes, sells).",
    "LAUNCHED": "Captures the official first release, shipment, or public start of a product, service, or initiative.",
    "DEVELOPED": "Shows design, R&D, or innovation origin of a technology, product, or capability. Includes 'researched' or 'created'.",
    "ADOPTED_BY": "Indicates that a technology or product has been taken up, deployed, or implemented by another entity.",
    "INVESTS_IN": "Represents the flow of capital or resources from one entity into another (equity, funding rounds, strategic investment).",
    "COLLABORATES_WITH": "Generic partnership, alliance, joint venture, or licensing relationship between entities.",
    "SUPPLIES": "Captures vendor–client supply-chain links or dependencies (provides to, sources from).",
    "HAS_REVENUE": "Associates an entity with a revenue amount or metric—actual, reported, or projected.",
    "INCREASED": "Expresses an upward change in a metric (revenue, market share, output) relative to a prior period or baseline.",
    "DECREASED": "Expresses a downward change in a metric relative to a prior period or baseline.",
    "RESULTED_IN": "Captures a causal relationship where one event or factor leads to a specific outcome (positive or negative).",
    "TARGETS": "Denotes a strategic objective, market segment, or customer group that an entity seeks to reach.",
    "PART_OF": "Expresses hierarchical membership or subset relationships (division, subsidiary, managed by, belongs to).",
    "DISCONTINUED": "Indicates official end-of-life, shutdown, or termination of a product, service, or relationship.",
    "SECURED": "Marks the successful acquisition of funding, contracts, assets, or rights by an entity.",
    
    # Learning/Education Domain (NEW - for educational/learning contexts)
    "STUDIES": "Indicates a learning or study relationship between a person and educational content, subject, or material (e.g., '李阳 STUDIES 历史', 'student STUDIES mathematics').",
    "COMMUNICATES_WITH": "Denotes direct communication, conversation, or interaction between two entities (e.g., '李阳 COMMUNICATES_WITH 张明'). Includes verbal and written communication.",
    "RECORDS_IN": "Indicates recording, writing, or documenting information in a medium (e.g., '李阳 RECORDS_IN 课本', 'user RECORDS_IN notebook').",
    "EVALUATES": "Expresses evaluation, assessment, or judgment of an entity (e.g., '老师 EVALUATES 作业', '李阳 EVALUATES 分封制').",
    "REFERENCES": "Denotes a reference, comparison, or analogy relationship (e.g., '游戏 REFERENCES 历史', 'book REFERENCES theory').",
    "CREATES": "Indicates creation, production, or generation of content, art, or artifacts (e.g., '李阳 CREATES 简笔画', 'artist CREATES painting').",
    
    # General
    "MENTIONS": "Denotes a reference or mention of an entity in a text or document.",

    # 移除了过于宽泛的谓语集合
    # "FEELS" : "Denotes a subjective opinion or feeling about an entity (e.g., 'I feel like X').Includes 'THINKS'.",
    # "HELPS" :"Express a action that make it easier or possible for (someone) to do something by offering one's services or resources. Includes 'assist', 'aid' and 'support' " ,
    # "IS_DOING" : "Denotes a subjective action or activity about an entity (e.g., 'I am doing X').Includes 'DOES'.",
    # "LIKES": "Express enjoy or approve of something or someone (e.g., 'I like roses').Includes 'LIKES'.",
    # "DISLIKES": "Express dislike or disapprove of something or someone (e.g., 'I dislike roses').Includes 'DISLIKES'.",
    # "HAS_ATTRIBUTE": "Express that an entity has a certain attribute (e.g., 'X has a red car').Includes 'HAS'.",

}

LABEL_DEFINITIONS: dict[str, dict[str, dict[str, str]]] = {
    "statement_labelling": {
        "FACT": dict(
            definition=(
                "Statements that are objective and can be independently "
                "verified or falsified through evidence."
            ),
            date_handling_guidance=(
                "These statements can be made up of multiple static and "
                "dynamic temporal events marking for example the start, end, "
                "and duration of the fact described statement."
            ),
            date_handling_example=(
                "'Company A owns Company B in 2022', 'X caused Y to happen', "
                "or 'John said X at Event' are verifiable facts which currently "
                "hold true unless we have a contradictory fact."
            ),
        ),
        "OPINION": dict(
            definition=(
                "Statements that contain personal opinions, feelings, values, "
                "or judgments that are not independently verifiable. It also "
                "includes hypothetical and speculative statements."
            ),
            date_handling_guidance=(
                "This statement is always static. It is a record of the date the "
                "opinion was made."
            ),
            date_handling_example=(
                "'I like Company A's strategy', 'X may have caused Y to happen', "
                "or 'The event felt like X' are opinions and down to the reporters "
                "interpretation."
            ),
        ),
        "PREDICTION": dict(
            definition=(
                "Uncertain statements about the future on something that might happen, "
                "a hypothetical outcome, unverified claims.  "
                "If the tense of the statement changed, the statement "
                "would then become a fact."
            ),
            date_handling_guidance=(
                "This statement is always static. It is a record of the date the "
                "prediction was made."
            ),
            date_handling_example=(
                "'It is rumoured that Dave will resign next month', 'Company A expects "
                "X to happen', or 'X suggests Y' are all predictions."
            ),
        ),
        "SUGGESTION": dict(
            definition=(
                "A proposal or recommendation for action, often implying a future course of conduct. "
                " It's not a statement of fact or a prediction, but rather an advised path. "
                "It's a suggestion for action that is not yet implemented."
            ),
            date_handling_guidance=(
                "This statement is always static."
            ),
            date_handling_example=(
                "'They should launch the new product next quarter', 'You could try a different approach', "
                "or 'I would recommend moving the headquarters to Berlin' are all suggestions."
            ),
        ),
    },
    "temporal_labelling": {
        "STATIC": dict(
            definition=(
                "Often past tense, think -ed verbs, describing single points-in-time. "
                "These statements are valid from the day they occurred and are never "
                "invalid. Refer to single points in time at which an event occurred, "
                "the fact X occurred on that date will always hold true."
            ),
            date_handling_guidance=(
                "The valid_at date is the date the event occurred. The invalid_at date "
                "is None."
            ),
            date_handling_example=(
                "'John was appointed CEO on 4th Jan 2024', 'Company A reported X percent "
                "growth from last FY', or 'X resulted in Y to happen' are valid the day "
                "they occurred and are never invalid."
            ),
        ),
        "DYNAMIC": dict(
            definition=(
                "Often present tense, think -ing verbs, describing a period of time. "
                "These statements are valid for a specific period of time and are usually "
                "invalidated by a Static fact marking the end of the event or start of a "
                "contradictory new one. The statement could already be referring to a "
                "discrete time period (invalid) or may be an ongoing relationship (not yet "
                "invalid)."
            ),
            date_handling_guidance=(
                "The valid_at date is the date the event started. The invalid_at date is "
                "the date the event or relationship ended, for ongoing events this is None."
            ),
            date_handling_example=(
                "'John is the CEO', 'Company A remains a market leader', or 'X is continuously "
                "causing Y to decrease' are valid from when the event started and are invalidated "
                "by a new event."
            ),
        ),
        "ATEMPORAL": dict(
            definition=(
                "Statements that will always hold true regardless of time therefore have no "
                "temporal bounds."
            ),
            date_handling_guidance=(
                "These statements are assumed to be atemporal and have no temporal bounds. Both "
                "their valid_at and invalid_at are None."
            ),
            date_handling_example=(
                "'A stock represents a unit of ownership in a company', 'The earth is round', or "
                "'Europe is a continent'. These statements are true regardless of time."
            ),
        ),
    },
}

class Predicate(StrEnum):
    """Enumeration of normalised predicates."""

    # Core Relationships
    IS_A = "IS_A"
    HAS_A = "HAS_A"
    LOCATED_IN = "LOCATED_IN"
    
    # Business/Corporate
    HOLDS_ROLE = "HOLDS_ROLE"
    PRODUCES = "PRODUCES"
    SELLS = "SELLS"
    LAUNCHED = "LAUNCHED"
    DEVELOPED = "DEVELOPED"
    ADOPTED_BY = "ADOPTED_BY"
    INVESTS_IN = "INVESTS_IN"
    COLLABORATES_WITH = "COLLABORATES_WITH"
    SUPPLIES = "SUPPLIES"
    HAS_REVENUE = "HAS_REVENUE"
    INCREASED = "INCREASED"
    DECREASED = "DECREASED"
    RESULTED_IN = "RESULTED_IN"
    TARGETS = "TARGETS"
    PART_OF = "PART_OF"
    DISCONTINUED = "DISCONTINUED"
    SECURED = "SECURED"
    
    # Learning/Education Domain
    STUDIES = "STUDIES"
    COMMUNICATES_WITH = "COMMUNICATES_WITH"
    RECORDS_IN = "RECORDS_IN"
    EVALUATES = "EVALUATES"
    REFERENCES = "REFERENCES"
    CREATES = "CREATES"
    
    # General
    MENTIONS = "MENTIONS"


class StatementType(StrEnum):
    FACT = "FACT"
    OPINION = "OPINION"
    PREDICTION = "PREDICTION"
    SUGGESTION = "SUGGESTION"

class TemporalInfo(StrEnum):
    ATEMPORAL = "ATEMPORAL"
    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"

# Relevance labelling for statements
class RelevenceInfo(StrEnum):
    RELEVANT = "RELEVANT"
    IRRELEVANT = "IRRELEVANT"

