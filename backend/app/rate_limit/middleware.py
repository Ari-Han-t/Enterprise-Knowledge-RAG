from collections.abc import Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.rate_limit.store import rate_limit_store


settings = get_settings()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        method = request.method.upper()

        if path == "/upload" and method == "POST":
            limited = self._enforce_scope(request, scope="upload", limit=settings.max_uploads_per_day, window_seconds=86400)
            if limited is not None:
                return limited

            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.max_file_size_bytes + 1024 * 32:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Uploaded file exceeds the 10MB limit"},
                )

        elif path in {"/ask", "/ask/stream"} and method == "POST":
            limited = self._enforce_scope(
                request,
                scope="query",
                limit=settings.max_queries_per_minute,
                window_seconds=60,
            )
            if limited is not None:
                return limited

            limited = self._enforce_scope(
                request,
                scope="query_day",
                limit=settings.max_queries_per_day,
                window_seconds=86400,
            )
            if limited is not None:
                return limited


        elif path in {"/auth/login", "/auth/signup"} and method == "POST":
            limited = self._enforce_scope(request, scope="auth", limit=10, window_seconds=60, user_scoped=False)
            if limited is not None:
                return limited

        return await call_next(request)

    def _enforce_scope(
        self,
        request: Request,
        *,
        scope: str,
        limit: int,
        window_seconds: int,
        user_scoped: bool = True,
    ) -> JSONResponse | None:
        ip_address = self._client_ip(request)
        request.state.client_ip = ip_address

        ip_result = rate_limit_store.hit(
            key=f"{scope}:ip:{ip_address}",
            limit=limit,
            window_seconds=window_seconds,
        )
        if not ip_result["allowed"]:
            return self._limit_response(scope, ip_result["retry_after"])

        user_id = self._extract_user_id(request) if user_scoped else None
        request.state.user_id = user_id
        if user_id:
            user_result = rate_limit_store.hit(
                key=f"{scope}:user:{user_id}",
                limit=limit,
                window_seconds=window_seconds,
            )
            if not user_result["allowed"]:
                return self._limit_response(scope, user_result["retry_after"])

        return None

    @staticmethod
    def _limit_response(scope: str, retry_after: int) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": f"{scope.capitalize()} rate limit exceeded. Please retry later."},
            headers={"Retry-After": str(retry_after)},
        )

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @staticmethod
    def _extract_user_id(request: Request) -> str | None:
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return None
        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            return payload.get("sub")
        except JWTError:
            return None

