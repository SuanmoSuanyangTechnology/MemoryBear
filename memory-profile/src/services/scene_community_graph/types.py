from src.common.errors import BizCode, LocalizedProfileError


def not_found() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.USER_NOT_FOUND,
        "analytics.scene_graph.user_not_found",
        detail_key="analytics.scene_graph.user_not_found",
        status_code=200,
    )


def bad_data() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.DB_ERROR,
        "analytics.scene_graph.bad_data",
        detail_key="analytics.scene_graph.bad_data",
        status_code=500,
    )


def capacity_error() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.INVALID_PARAMETER,
        "analytics.scene_graph.capacity_exceeded",
        detail_key="analytics.scene_graph.capacity_exceeded",
        status_code=400,
    )


def unavailable() -> LocalizedProfileError:
    return LocalizedProfileError(
        BizCode.SERVICE_UNAVAILABLE,
        "analytics.scene_graph.unavailable",
        detail_key="analytics.scene_graph.unavailable",
        status_code=503,
    )
