class ApiError(Exception):
    """业务错误：任何路由抛出后由统一错误处理器转成 JSON 响应。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status
