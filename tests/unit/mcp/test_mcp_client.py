from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from mini_code_agent.mcp.client import McpStdioClient
from mini_code_agent.mcp.contracts import schema_sha256
from mini_code_agent.mcp.models import (
    MCP_PROTOCOL_VERSION,
    McpCallError,
    McpCallErrorCode,
    McpCallResult,
    McpConnectionApprovalRequest,
    McpConnectionError,
    McpConnectionErrorCode,
    McpInitializeSnapshot,
    McpLifecycleState,
    McpLimits,
    McpRemoteTool,
    McpServerProfile,
    McpToolGrant,
    McpToolPage,
)
from mini_code_agent.policy.models import RiskLevel
from mini_code_agent.tools.base import SideEffect

PYTHON_EXECUTABLE = str(Path(sys.executable).resolve())


def input_schema() -> dict[str, JsonValue]:
    return {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def grant_for(
    *,
    side_effect: SideEffect = SideEffect.READ_ONLY,
) -> McpToolGrant:
    return McpToolGrant(
        remote_name="status",
        local_name="mcp_status",
        description="Read status.",
        side_effect=side_effect,
        risk=(RiskLevel.LOW if side_effect is SideEffect.READ_ONLY else RiskLevel.HIGH),
        input_schema_sha256=schema_sha256(input_schema()),
    )


def profile_for(
    tmp_path: Path,
    *,
    grant: McpToolGrant | None = None,
    limits: McpLimits | None = None,
) -> McpServerProfile:
    return McpServerProfile(
        server_id="local-test",
        command=PYTHON_EXECUTABLE,
        args=("-m", "example_server"),
        cwd=tmp_path.resolve(),
        expected_server_name="mini-code-agent-test",
        expected_server_version="1.0.0",
        grants=(grant or grant_for(),),
        limits=limits or McpLimits(),
    )


def valid_initialized(**changes: object) -> McpInitializeSnapshot:
    payload: dict[str, object] = {
        "protocol_version": MCP_PROTOCOL_VERSION,
        "server_name": "mini-code-agent-test",
        "server_version": "1.0.0",
        "has_tools": True,
        "tools_list_changed": False,
    }
    payload.update(changes)
    return McpInitializeSnapshot.model_validate(payload)


def valid_page() -> McpToolPage:
    return McpToolPage(
        tools=(
            McpRemoteTool(
                name="status",
                input_schema=input_schema(),
            ),
        )
    )


class RecordingApprover:
    def __init__(
        self,
        events: list[str],
        *,
        approved: object = True,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self._events = events
        self._approved = approved
        self._delay = delay
        self._error = error
        self.requests: list[McpConnectionApprovalRequest] = []

    async def approve(self, request: McpConnectionApprovalRequest) -> bool:
        self._events.append("approval")
        self.requests.append(request)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return cast(bool, self._approved)


class FakeSession:
    def __init__(
        self,
        events: list[str],
        *,
        initialized: McpInitializeSnapshot | None = None,
        page: McpToolPage | None = None,
        initialize_delay: float = 0,
        list_delay: float = 0,
        call_delay: float = 0,
        close_delay: float = 0,
        call_error: Exception | None = None,
        require_same_task_close: bool = False,
    ) -> None:
        self._events = events
        self._initialized = initialized or valid_initialized()
        self._page = page or valid_page()
        self._initialize_delay = initialize_delay
        self._list_delay = list_delay
        self._call_delay = call_delay
        self._close_delay = close_delay
        self._call_error = call_error
        self._require_same_task_close = require_same_task_close
        self._owner_task: asyncio.Task[object] | None = None
        self.close_count = 0
        self.call_count = 0
        self.active_calls = 0
        self.max_active_calls = 0

    async def initialize(self) -> McpInitializeSnapshot:
        self._events.append("initialize")
        self._owner_task = asyncio.current_task()
        if self._initialize_delay:
            await asyncio.sleep(self._initialize_delay)
        return self._initialized

    async def list_tools(self) -> McpToolPage:
        self._events.append("list")
        if self._list_delay:
            await asyncio.sleep(self._list_delay)
        return self._page

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> McpCallResult:
        del name, arguments
        self._events.append("call")
        self.call_count += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self._call_delay:
                await asyncio.sleep(self._call_delay)
            if self._call_error is not None:
                raise self._call_error
            return McpCallResult(text=("clean",))
        finally:
            self.active_calls -= 1

    async def aclose(self) -> None:
        self._events.append("close")
        self.close_count += 1
        if self._require_same_task_close and asyncio.current_task() is not self._owner_task:
            raise RuntimeError("Session closed from a different task.")
        if self._close_delay:
            await asyncio.sleep(self._close_delay)


class RecordingFactory:
    def __init__(
        self,
        events: list[str],
        session: FakeSession,
        *,
        delay: float = 0,
        error: Exception | None = None,
    ) -> None:
        self._events = events
        self._session = session
        self._delay = delay
        self._error = error
        self.open_count = 0

    async def open(self, profile: McpServerProfile) -> FakeSession:
        del profile
        self._events.append("open")
        self.open_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._session


@pytest.mark.asyncio
async def test_connect_requires_approval_before_process_open(tmp_path: Path) -> None:
    events: list[str] = []
    session = FakeSession(events)
    factory = RecordingFactory(events, session)
    approver = RecordingApprover(events)
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=approver,
        factory=factory,
    )

    await client.connect()

    assert events == ["approval", "open", "initialize", "list"]
    assert client.state is McpLifecycleState.READY
    assert tuple(item.remote_name for item in client.verified_tools) == ("status",)
    assert len(approver.requests) == 1
    await client.aclose()
    assert events[-1] == "close"
    assert client.state is McpLifecycleState.CLOSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "error"),
    [
        (False, None),
        ("yes", None),
        (True, RuntimeError("secret detail")),
    ],
)
async def test_denied_invalid_or_failed_approval_never_opens(
    tmp_path: Path,
    approved: object,
    error: Exception | None,
) -> None:
    events: list[str] = []
    session = FakeSession(events)
    factory = RecordingFactory(events, session)
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=RecordingApprover(events, approved=approved, error=error),
        factory=factory,
    )

    with pytest.raises(McpConnectionError) as caught:
        await client.connect()

    assert caught.value.code is McpConnectionErrorCode.CONNECTION_NOT_APPROVED
    assert "secret detail" not in str(caught.value)
    assert factory.open_count == 0
    assert client.state is McpLifecycleState.FAILED


@pytest.mark.asyncio
async def test_approval_timeout_fails_before_open(tmp_path: Path) -> None:
    events: list[str] = []
    limits = McpLimits(approval_timeout_seconds=0.1)
    session = FakeSession(events)
    factory = RecordingFactory(events, session)
    client = McpStdioClient(
        profile_for(tmp_path, limits=limits),
        approver=RecordingApprover(events, delay=0.2),
        factory=factory,
    )

    with pytest.raises(McpConnectionError) as caught:
        await client.connect()

    assert caught.value.code is McpConnectionErrorCode.CONNECTION_NOT_APPROVED
    assert factory.open_count == 0


@pytest.mark.asyncio
async def test_contract_failure_closes_and_admits_no_tools(tmp_path: Path) -> None:
    events: list[str] = []
    session = FakeSession(
        events,
        initialized=valid_initialized(server_name="replacement"),
    )
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )

    with pytest.raises(McpConnectionError) as caught:
        await client.connect()

    assert caught.value.code is McpConnectionErrorCode.IDENTITY_MISMATCH
    assert client.verified_tools == ()
    assert session.close_count == 1
    assert client.state is McpLifecycleState.FAILED


@pytest.mark.asyncio
async def test_startup_and_listing_timeouts_close_the_session(tmp_path: Path) -> None:
    limits = McpLimits(startup_timeout_seconds=0.1, list_timeout_seconds=0.1)

    startup_events: list[str] = []
    startup_session = FakeSession(startup_events, initialize_delay=0.2)
    startup = McpStdioClient(
        profile_for(tmp_path, limits=limits),
        approver=RecordingApprover(startup_events),
        factory=RecordingFactory(startup_events, startup_session),
    )
    with pytest.raises(McpConnectionError) as caught:
        await startup.connect()
    assert caught.value.code is McpConnectionErrorCode.CONNECTION_TIMEOUT
    assert startup_session.close_count == 1

    list_events: list[str] = []
    list_session = FakeSession(list_events, list_delay=0.2)
    listing = McpStdioClient(
        profile_for(tmp_path, limits=limits),
        approver=RecordingApprover(list_events),
        factory=RecordingFactory(list_events, list_session),
    )
    with pytest.raises(McpConnectionError) as caught:
        await listing.connect()
    assert caught.value.code is McpConnectionErrorCode.CONNECTION_TIMEOUT
    assert list_session.close_count == 1


@pytest.mark.asyncio
async def test_connect_is_single_use_and_close_is_idempotent(tmp_path: Path) -> None:
    events: list[str] = []
    session = FakeSession(events)
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()

    with pytest.raises(McpConnectionError):
        await client.connect()
    await client.aclose()
    await client.aclose()

    assert session.close_count == 1


@pytest.mark.asyncio
async def test_session_closes_in_the_task_that_opened_it(tmp_path: Path) -> None:
    events: list[str] = []
    session = FakeSession(events, require_same_task_close=True)
    client = McpStdioClient(
        profile_for(tmp_path),
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()

    await client.aclose()

    assert client.state is McpLifecycleState.CLOSED
    assert session.close_count == 1


@pytest.mark.asyncio
async def test_calls_are_serialized_and_never_retried(tmp_path: Path) -> None:
    events: list[str] = []
    session = FakeSession(events, call_delay=0.03)
    profile = profile_for(tmp_path)
    client = McpStdioClient(
        profile,
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()

    first, second = await asyncio.gather(
        client.call(profile.grants[0], {"path": "one"}),
        client.call(profile.grants[0], {"path": "two"}),
    )

    assert first.text == ("clean",)
    assert second.text == ("clean",)
    assert session.call_count == 2
    assert session.max_active_calls == 1
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("side_effect", "expected_code"),
    [
        (SideEffect.READ_ONLY, McpCallErrorCode.TIMEOUT),
        (SideEffect.WRITE, McpCallErrorCode.COMPLETION_UNKNOWN),
    ],
)
async def test_call_timeout_closes_connection_without_retry(
    tmp_path: Path,
    side_effect: SideEffect,
    expected_code: McpCallErrorCode,
) -> None:
    events: list[str] = []
    grant = grant_for(side_effect=side_effect)
    limits = McpLimits(call_timeout_seconds=0.1)
    profile = profile_for(tmp_path, grant=grant, limits=limits)
    session = FakeSession(events, call_delay=0.2)
    client = McpStdioClient(
        profile,
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()

    with pytest.raises(McpCallError) as caught:
        await client.call(grant, {"path": "one"})

    assert caught.value.code is expected_code
    assert session.call_count == 1
    assert session.close_count == 1
    assert client.state is McpLifecycleState.FAILED


@pytest.mark.asyncio
async def test_raw_call_failure_is_static_and_disconnects(tmp_path: Path) -> None:
    events: list[str] = []
    profile = profile_for(tmp_path)
    session = FakeSession(
        events,
        call_error=RuntimeError("server leaked secret"),
    )
    client = McpStdioClient(
        profile,
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()

    with pytest.raises(McpCallError) as caught:
        await client.call(profile.grants[0], {"path": "one"})

    assert caught.value.code is McpCallErrorCode.FAILED
    assert "server leaked secret" not in str(caught.value)
    assert session.call_count == 1
    assert session.close_count == 1


@pytest.mark.asyncio
async def test_call_cancellation_propagates_after_cleanup(tmp_path: Path) -> None:
    events: list[str] = []
    profile = profile_for(tmp_path)
    session = FakeSession(events, call_delay=10)
    client = McpStdioClient(
        profile,
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()
    task = asyncio.create_task(client.call(profile.grants[0], {"path": "one"}))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert session.close_count == 1
    assert client.state is McpLifecycleState.FAILED


@pytest.mark.asyncio
async def test_unknown_grant_and_closed_client_never_call_session(tmp_path: Path) -> None:
    events: list[str] = []
    profile = profile_for(tmp_path)
    session = FakeSession(events)
    client = McpStdioClient(
        profile,
        approver=RecordingApprover(events),
        factory=RecordingFactory(events, session),
    )
    await client.connect()
    unknown = grant_for().model_copy(update={"remote_name": "other"})

    with pytest.raises(McpCallError) as caught:
        await client.call(unknown, {"path": "one"})
    assert caught.value.code is McpCallErrorCode.FAILED

    await client.aclose()
    with pytest.raises(McpCallError) as caught:
        await client.call(profile.grants[0], {"path": "one"})
    assert caught.value.code is McpCallErrorCode.NOT_CONNECTED
    assert session.call_count == 0
