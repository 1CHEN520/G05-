"""统一错误码契约（issue G05-02 冻结的接口契约之一）。

## 错误体格式

所有非 2xx 响应统一为::

    {
      "code": "G05-2001",     # 业务错误码，机器可判定
      "message": "设备编号已存在",  # 人类可读的一句话
      "detail": {...}          # 结构化补充信息（可选）
    }

## 编码规则

    G05-1xxx  参数错误   —— 请求本身不合法，客户端改了就对
    G05-2xxx  业务错误   —— 请求合法但业务规则不允许，客户端改参数没用
    G05-5xxx  系统错误   —— 服务端问题，客户端重试可能有用

同一段数字在不同错误之间保持唯一，**只增不改**：
已经发出去的码不能改语义，也不能复用给别的错误 ——
客户端可能已经在按码做分支判断了。

## 怎么用

在路由里抛 :class:`ApiError`（而不是裸 ``HTTPException``）::

    from g05.schemas.errors import ApiError, E_DUPLICATE_DEVICE

    raise ApiError(E_DUPLICATE_DEVICE, detail={"device_code": payload.device_code})

或者直接抛 :class:`fastapi.HTTPException` 并带上 ``detail`` 字典 ——
:func:`g05.main.http_exception_handler` 会自动把 ``detail`` 里���
``code`` 字段提出来，转成本文件约定的格式。

C4 的 issue #33「统一异常处理与错误码」落地时，所有路由应改用本文件的码。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ApiError",
    "ErrorOut",
    "ErrorCode",
    "CODES",
    "describe",
    "http_status_of",
    "is_valid_code",
]


class ErrorCode:
    """错误码常量与元数据。

    刻意做成普通类而不是 ``Enum``：错误码需要能被直接当字符串用
    （放进 JSON、放日志、给前端判断），``Enum`` 会多一层 ``.value`` 的心智负担。
    """

    # ---------------- 1xxx 参数错误 ----------------
    INVALID_PARAM = "G05-1001"  # 参数格式/取值不合法
    MISSING_PARAM = "G05-1002"  # 必填参数缺失
    PAGE_OUT_OF_RANGE = "G05-1003"  # 分页参数越界
    UNSUPPORTED_FILE_TYPE = "G05-1004"  # 文件类型不支持
    FILE_TOO_LARGE = "G05-1005"  # 文件超出大小上限
    INVALID_ENUM_VALUE = "G05-1006"  # 枚举取值不在允许集合内
    INVALID_JSON = "G05-1007"  # 请求体不是合法 JSON

    # ---------------- 2xxx 业务错误 ----------------
    DEVICE_NOT_FOUND = "G05-2001"  # 设备不存在
    POINT_NOT_FOUND = "G05-2002"  # 测点不存在
    DUPLICATE_DEVICE = "G05-2003"  # 设备编号已存在
    DUPLICATE_POINT = "G05-2004"  # 测点编号已存在
    DEVICE_HAS_ACTIVE_POINTS = "G05-2005"  # 设备下仍有活跃测点，不可删
    POINT_DEVICE_NOT_FOUND = "G05-2006"  # 测点引用的设备不存在
    LINEAGE_NODE_NOT_FOUND = "G05-2007"  # 血缘节点不存在
    LINEAGE_SELF_LOOP = "G05-2008"  # 血缘自环被拒
    LINEAGE_CYCLE_DETECTED = "G05-2009"  # 血缘成环
    IMPORT_VALIDATION_FAILED = "G05-2010"  # 导入数据校验不通过
    IMPORT_ROLLED_BACK = "G05-2011"  # 导入整批回滚
    AUDIT_IMMUTABLE_VIOLATION = "G05-2012"  # 试图修改审计记录被拒
    RENAME_CONFLICT = "G05-2013"  # 改名冲突（版本链不一致）

    # ---------------- 5xxx 系统错误 ----------------
    INTERNAL_ERROR = "G05-5001"  # 未预期的服务端异常
    DB_ERROR = "G05-5002"  # 数据库操作失败
    DB_SCHEMA_ERROR = "G05-5003"  # 表结构缺失或未初始化
    SERVICE_UNAVAILABLE = "G05-5004"  # 依赖不可用


#: 错误码 → 建议 HTTP 状态码。
#:
#: 约定：**同一段数字的语义边界要一致**——
#: 1xxx 统一 4xx（文件过大用 413），
#: 2xxx 统一按"资源不存在 404 / 状态冲突 409 / 规则不满足 422"细分，
#: 5xxx 统一 500（依赖不可用 503）。
#:
#: **1xxx 里为什么有 422**：请求体/查询参数的**框架级校验失败**
#: （缺字段、类型不对、枚举越界、分页越界）统一走 422 —— 这是 FastAPI 对
#: "语法对但语义不合法"的既定语义，参考实现与 C5 的 99 条用例一直遵循这个约定。
#: 路由代码**显式**抛的 400（文件类型不对之类）才用 400。
HTTP_STATUS: dict[str, int] = {
    # 1xxx 参数
    ErrorCode.INVALID_PARAM: 422,
    ErrorCode.MISSING_PARAM: 422,
    ErrorCode.PAGE_OUT_OF_RANGE: 422,
    ErrorCode.UNSUPPORTED_FILE_TYPE: 400,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.INVALID_ENUM_VALUE: 422,
    ErrorCode.INVALID_JSON: 422,
    # 2xxx 业务
    ErrorCode.DEVICE_NOT_FOUND: 404,
    ErrorCode.POINT_NOT_FOUND: 404,
    ErrorCode.DUPLICATE_DEVICE: 409,
    ErrorCode.DUPLICATE_POINT: 409,
    ErrorCode.DEVICE_HAS_ACTIVE_POINTS: 409,
    ErrorCode.POINT_DEVICE_NOT_FOUND: 404,
    ErrorCode.LINEAGE_NODE_NOT_FOUND: 404,
    ErrorCode.LINEAGE_SELF_LOOP: 422,
    ErrorCode.LINEAGE_CYCLE_DETECTED: 422,
    ErrorCode.IMPORT_VALIDATION_FAILED: 422,
    ErrorCode.IMPORT_ROLLED_BACK: 422,
    ErrorCode.AUDIT_IMMUTABLE_VIOLATION: 409,
    ErrorCode.RENAME_CONFLICT: 409,
    # 5xxx 系统
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.DB_ERROR: 500,
    ErrorCode.DB_SCHEMA_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
}

#: 错误码 → 一句话说明（给文档和前端提示用）。
DESCRIPTIONS: dict[str, str] = {
    ErrorCode.INVALID_PARAM: "参数格式或取值不合法",
    ErrorCode.MISSING_PARAM: "缺少必填参数",
    ErrorCode.PAGE_OUT_OF_RANGE: "分页参数越界",
    ErrorCode.UNSUPPORTED_FILE_TYPE: "不支持的文件类型",
    ErrorCode.FILE_TOO_LARGE: "文件超出大小上限",
    ErrorCode.INVALID_ENUM_VALUE: "枚举取值不在允许集合内",
    ErrorCode.INVALID_JSON: "请求体不是合法 JSON",
    ErrorCode.DEVICE_NOT_FOUND: "设备不存在",
    ErrorCode.POINT_NOT_FOUND: "测点不存在",
    ErrorCode.DUPLICATE_DEVICE: "设备编号已存在",
    ErrorCode.DUPLICATE_POINT: "测点编号已存在",
    ErrorCode.DEVICE_HAS_ACTIVE_POINTS: "设备下仍有活跃测点，不可删除",
    ErrorCode.POINT_DEVICE_NOT_FOUND: "测点引用的设备不存在",
    ErrorCode.LINEAGE_NODE_NOT_FOUND: "血缘节点不存在",
    ErrorCode.LINEAGE_SELF_LOOP: "血缘自环被拒绝",
    ErrorCode.LINEAGE_CYCLE_DETECTED: "血缘形成环",
    ErrorCode.IMPORT_VALIDATION_FAILED: "导入数据校验未通过",
    ErrorCode.IMPORT_ROLLED_BACK: "导入整批已回滚",
    ErrorCode.AUDIT_IMMUTABLE_VIOLATION: "审计记录不可修改",
    ErrorCode.RENAME_CONFLICT: "改名冲突，版本链不一致",
    ErrorCode.INTERNAL_ERROR: "服务内部错误",
    ErrorCode.DB_ERROR: "数据库操作失败",
    ErrorCode.DB_SCHEMA_ERROR: "表结构缺失或未初始化",
    ErrorCode.SERVICE_UNAVAILABLE: "依赖服务不可用",
}

#: 全部已定义的错误码（供自检脚本与文档生成使用）。
CODES: tuple[str, ...] = tuple(sorted(set(HTTP_STATUS) | set(DESCRIPTIONS)))


def http_status_of(code: str) -> int:
    """查错误码对应的 HTTP 状态码；未登记的码一律 500。

    未登记的码说明是调用方写错了 —— 宁可返回 500 也不要猜一个状态码，
    因为 4xx 会让客户端以为"我改一下参数重试就行"。
    """
    return HTTP_STATUS.get(code, 500)


def is_valid_code(code: str) -> bool:
    """错误码是否已登记。"""
    return code in HTTP_STATUS or code in DESCRIPTIONS


def describe(code: str) -> str:
    """错误码的一句话说明。"""
    return DESCRIPTIONS.get(code, "未登记的错误码")


class ErrorOut(BaseModel):
    """统一错误响应体。

    这是所有非 2xx 响应的**唯一**格式约定。
    """

    code: str = Field(..., description="业务错误码，如 G05-2001")
    message: str = Field(..., description="人类可读的一句话说明")
    detail: dict[str, Any] = Field(default_factory=dict, description="结构化补充信息")


class ApiError(Exception):
    """带业务错误码的异常。

    路由层用它替代裸 ``HTTPException``，由全局异常处理器转成
    ``{code, message, detail}`` 三字段的错误体。
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or describe(code)
        self.detail = detail or {}
        self.status_code = http_status_of(code)
        super().__init__(f"{code}: {self.message}")
