from error_core.envelope import fail as _fail
from error_core.envelope import success


def ok(data=None, msg: str = "success") -> dict:
    return success(data=data, msg=msg)


def fail(code: int, msg: str) -> dict:
    return _fail(code=code, msg=msg)
