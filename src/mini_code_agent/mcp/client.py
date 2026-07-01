from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Self

from pydantic import JsonValue

from mini_code_agent.mcp.contracts import (
    VerifiedMcpTool,
    verify_server_contract,
    verify_tool_contracts,
)
from mini_code_agent.mcp.models import (
    McpCallError,
    McpCallErrorCode,
    McpCallResult,
    McpConnectionApprover,
    McpConnectionError,
    McpConnectionErrorCode,
    McpLifecycleState,
    McpServerProfile,
    McpToolGrant,
)
from mini_code_agent.mcp.sdk import (
    McpSession,
    McpSessionFactory,
    OfficialStdioSessionFactory,
)
from mini_code_agent.tools.base import SideEffect


class McpStdioClient:
    def __init__(
        self,
        profile: McpServerProfile,
        *,
        approver: McpConnectionApprover,
        factory: McpSessionFactory | None = None,
    ) -> None:
        self._profile = profile
        self._approver = approver
        self._factory = factory or OfficialStdioSessionFactory()
        self._state = McpLifecycleState.NEW
        self._session: McpSession | None = None
        self._verified_tools: tuple[VerifiedMcpTool, ...] = ()
        self._call_lock = asyncio.Lock()

    @property
    def profile(self) -> McpServerProfile:
        return self._profile

    @property
    def state(self) -> McpLifecycleState:
        return self._state

    @property
    def verified_tools(self) -> tuple[VerifiedMcpTool, ...]:
        return self._verified_tools

    async def connect(self) -> None:
        if self._state is not McpLifecycleState.NEW:
            raise McpConnectionError(McpConnectionErrorCode.CONNECTION_FAILED)
        self._state = McpLifecycleState.APPROVING
        try:
            async with asyncio.timeout(self._profile.limits.approval_timeout_seconds):
                approved = await self._approver.approve(self._profile.approval_request())
        except asyncio.CancelledError:
            self._state = McpLifecycleState.FAILED
            raise
        except Exception:
            self._state = McpLifecycleState.FAILED
            raise McpConnectionError(McpConnectionErrorCode.CONNECTION_NOT_APPROVED) from None
        if approved is not True:
            self._state = McpLifecycleState.FAILED
            raise McpConnectionError(McpConnectionErrorCode.CONNECTION_NOT_APPROVED)

        try:
            self._state = McpLifecycleState.CONNECTING
            async with asyncio.timeout(self._profile.limits.startup_timeout_seconds):
                self._session = await self._factory.open(self._profile)

            self._state = McpLifecycleState.VERIFYING
            async with asyncio.timeout(self._profile.limits.startup_timeout_seconds):
                initialized = await self._session.initialize()
            verify_server_contract(self._profile, initialized)

            async with asyncio.timeout(self._profile.limits.list_timeout_seconds):
                page = await self._session.list_tools()
            verified = verify_tool_contracts(self._profile, page)
        except asyncio.CancelledError:
            self._state = McpLifecycleState.FAILED
            await self._close_session_safely()
            raise
        except TimeoutError:
            self._state = McpLifecycleState.FAILED
            await self._close_session_safely()
            raise McpConnectionError(McpConnectionErrorCode.CONNECTION_TIMEOUT) from None
        except McpConnectionError:
            self._state = McpLifecycleState.FAILED
            await self._close_session_safely()
            raise
        except Exception:
            self._state = McpLifecycleState.FAILED
            await self._close_session_safely()
            raise McpConnectionError(McpConnectionErrorCode.CONNECTION_FAILED) from None

        self._verified_tools = verified
        self._state = McpLifecycleState.READY

    async def call(
        self,
        grant: McpToolGrant,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult:
        if self._state is not McpLifecycleState.READY or self._session is None:
            raise McpCallError(McpCallErrorCode.NOT_CONNECTED)
        if grant not in tuple(item.grant for item in self._verified_tools):
            raise McpCallError(McpCallErrorCode.FAILED)

        async with self._call_lock:
            session = self._ready_session()
            if session is None:
                raise McpCallError(McpCallErrorCode.NOT_CONNECTED)
            try:
                async with asyncio.timeout(self._profile.limits.call_timeout_seconds):
                    return await session.call_tool(
                        grant.remote_name,
                        arguments,
                    )
            except asyncio.CancelledError:
                self._state = McpLifecycleState.FAILED
                await self._close_session_safely()
                raise
            except TimeoutError:
                self._state = McpLifecycleState.FAILED
                await self._close_session_safely()
                code = (
                    McpCallErrorCode.TIMEOUT
                    if grant.side_effect is SideEffect.READ_ONLY
                    else McpCallErrorCode.COMPLETION_UNKNOWN
                )
                raise McpCallError(code) from None
            except McpCallError:
                raise
            except Exception:
                self._state = McpLifecycleState.FAILED
                await self._close_session_safely()
                raise McpCallError(McpCallErrorCode.FAILED) from None

    async def aclose(self) -> None:
        async with self._call_lock:
            if self._state is McpLifecycleState.CLOSED:
                return
            self._state = McpLifecycleState.CLOSING
            closed_cleanly = await self._close_session_safely()
            self._state = McpLifecycleState.CLOSED
        if not closed_cleanly:
            raise McpConnectionError(McpConnectionErrorCode.CLOSE_FAILED)

    def _ready_session(self) -> McpSession | None:
        if self._state is not McpLifecycleState.READY:
            return None
        return self._session

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.aclose()

    async def _close_session_safely(self) -> bool:
        session = self._session
        self._session = None
        self._verified_tools = ()
        if session is None:
            return True

        try:
            async with asyncio.timeout(self._profile.limits.close_timeout_seconds):
                await session.aclose()
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return True
