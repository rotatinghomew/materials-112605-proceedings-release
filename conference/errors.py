"""业务错误与 HTTP 状态码的对应关系。"""


class ServiceError(Exception):
    status = 400

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(ServiceError):
    status = 404


class Conflict(ServiceError):
    status = 409


class Unavailable(ServiceError):
    status = 503


class ValidationFailed(ServiceError):
    status = 422
