from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
from typing import Protocol, cast

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from pydantic import JsonValue, ValidationError

from mini_code_agent import __version__
from mini_code_agent.domain.json import FrozenJsonValue, thaw_json_mapping
from mini_code_agent.mcp.models import (
    McpCallError,
    McpCallErrorCode,
    McpCallResult,
    McpConnectionError,
    McpConnectionErrorCode,
    McpInitializeSnapshot,
    McpRemoteTool,
    McpServerProfile,
    McpToolPage,
)

_MAX_SNAPSHOT_TOOLS = 128
_MAX_SNAPSHOT_TEXT_BLOCKS = 128
_MAX_SNAPSHOT_TEXT_CHARS = 524_288


class McpSession(Protocol):
    async def initialize(self) -> McpInitializeSnapshot: ...

    async def list_tools(self) -> McpToolPage: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult: ...

    async def aclose(self) -> None: ...


class McpSessionFactory(Protocol):
    async def open(self, profile: McpServerProfile) -> McpSession: ...


def build_stdio_parameters(profile: McpServerProfile) -> StdioServerParameters:
    try:
        profile.revalidate_launch_paths()
    except ValueError:
        raise McpConnectionError(McpConnectionErrorCode.CONNECTION_FAILED) from None
    return StdioServerParameters(
        command=profile.command,
        args=list(profile.args),
        env={key: secret.get_secret_value() for key, secret in profile.environment.items()},
        cwd=profile.cwd,
        encoding="utf-8",
        encoding_error_handler="strict",
    )


def snapshot_initialize_result(
    result: types.InitializeResult,
) -> McpInitializeSnapshot:
    tools = result.capabilities.tools
    if not isinstance(result.protocolVersion, str):
        raise McpConnectionError(McpConnectionErrorCode.CONNECTION_FAILED)
    try:
        return McpInitializeSnapshot(
            protocol_version=result.protocolVersion,
            server_name=result.serverInfo.name,
            server_version=result.serverInfo.version,
            has_tools=tools is not None,
            tools_list_changed=bool(tools is not None and tools.listChanged is True),
        )
    except ValidationError:
        raise McpConnectionError(McpConnectionErrorCode.CONNECTION_FAILED) from None


def snapshot_tool_page(result: types.ListToolsResult) -> McpToolPage:
    if len(result.tools) > _MAX_SNAPSHOT_TOOLS:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_LISTING_TOO_LARGE)
    try:
        return McpToolPage(
            tools=tuple(
                McpRemoteTool.model_validate(
                    {
                        "name": tool.name,
                        "input_schema": tool.inputSchema,
                        "output_schema": tool.outputSchema,
                        "task_support": (
                            tool.execution.taskSupport
                            if tool.execution is not None and tool.execution.taskSupport is not None
                            else "forbidden"
                        ),
                    }
                )
                for tool in result.tools
            ),
            next_cursor=result.nextCursor,
        )
    except ValidationError:
        raise McpConnectionError(McpConnectionErrorCode.TOOL_SCHEMA_INVALID) from None


def snapshot_call_result(result: types.CallToolResult) -> McpCallResult:
    if len(result.content) > _MAX_SNAPSHOT_TEXT_BLOCKS:
        raise McpCallError(McpCallErrorCode.RESULT_TOO_LARGE)
    text: list[str] = []
    text_chars = 0
    for block in result.content:
        if not isinstance(block, types.TextContent):
            raise McpCallError(McpCallErrorCode.RESULT_UNSUPPORTED)
        text_chars += len(block.text)
        if text_chars > _MAX_SNAPSHOT_TEXT_CHARS:
            raise McpCallError(McpCallErrorCode.RESULT_TOO_LARGE)
        text.append(block.text)
    try:
        return McpCallResult.model_validate(
            {
                "text": tuple(text),
                "structured_content": result.structuredContent,
                "is_error": result.isError,
            }
        )
    except (TypeError, ValidationError):
        raise McpCallError(McpCallErrorCode.RESULT_INVALID) from None


class OfficialStdioSessionFactory:
    async def open(self, profile: McpServerProfile) -> McpSession:
        ready = asyncio.get_running_loop().create_future()
        close_event = asyncio.Event()
        worker = asyncio.create_task(
            _own_stdio_session(profile, ready, close_event),
            name=f"mcp-stdio-{profile.server_id}",
        )
        try:
            session = await ready
        except BaseException:
            if not ready.done():
                ready.cancel()
            close_event.set()
            worker.cancel()
            with suppress(BaseException):
                await worker
            raise
        return _OfficialStdioSession(
            worker,
            close_event,
            session,
            call_timeout_seconds=profile.limits.call_timeout_seconds,
        )


async def _own_stdio_session(
    profile: McpServerProfile,
    ready: asyncio.Future[ClientSession],
    close_event: asyncio.Event,
) -> None:
    stack = AsyncExitStack()
    try:
        errlog = stack.enter_context(
            open(os.devnull, "w", encoding="utf-8"),  # noqa: SIM115
        )
        read_stream, write_stream = await stack.enter_async_context(
            stdio_client(
                build_stdio_parameters(profile),
                errlog=errlog,
            )
        )
        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=profile.limits.call_timeout_seconds),
                client_info=types.Implementation(
                    name="mini-code-agent",
                    version=__version__,
                ),
            )
        )
        if not ready.done():
            ready.set_result(session)
        await close_event.wait()
    except BaseException as exc:
        if not ready.done():
            ready.set_exception(exc)
        raise
    finally:
        await stack.aclose()


class _OfficialStdioSession:
    def __init__(
        self,
        worker: asyncio.Task[None],
        close_event: asyncio.Event,
        session: ClientSession,
        *,
        call_timeout_seconds: float,
    ) -> None:
        self._worker = worker
        self._close_event = close_event
        self._session = session
        self._call_timeout = timedelta(seconds=call_timeout_seconds)
        self._closed = False

    async def initialize(self) -> McpInitializeSnapshot:
        self._require_open()
        return snapshot_initialize_result(await self._session.initialize())

    async def list_tools(self) -> McpToolPage:
        self._require_open()
        return snapshot_tool_page(await self._session.list_tools())

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult:
        self._require_open()
        frozen = cast(Mapping[str, FrozenJsonValue], arguments)
        result = await self._session.call_tool(
            name,
            arguments=thaw_json_mapping(frozen),
            read_timeout_seconds=self._call_timeout,
        )
        return snapshot_call_result(result)

    async def aclose(self) -> None:
        self._closed = True
        self._close_event.set()
        await asyncio.shield(self._worker)

    def _require_open(self) -> None:
        if self._closed or self._worker.done():
            raise McpCallError(McpCallErrorCode.NOT_CONNECTED)
