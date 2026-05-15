"""Controller state-machine tests. No real CLI, no real Qt event loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from PyQt6.QtCore import QCoreApplication

from vpnpilot.cli import CLIResult, ProtonCLI
from vpnpilot.controller import (
    MIN_REFRESH_INTERVAL_SECONDS,
    POLL_INTERVAL_SECONDS,
    Controller,
)
from vpnpilot.detect import Detector
from vpnpilot.preset import PresetFlags, PresetStore, PresetTarget, TargetKind
from vpnpilot.state import AuthState, ConnectionInfo, ConnState


@dataclass
class ScriptedDetector(Detector):
    """Yields a queue of ConnectionInfo answers, last one repeats."""

    answers: list[ConnectionInfo]
    calls: int = 0

    async def detect(self) -> ConnectionInfo:
        i = min(self.calls, len(self.answers) - 1)
        self.calls += 1
        return self.answers[i]


class BlockingDetector(Detector):
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def detect(self) -> ConnectionInfo:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ConnectionInfo(state=ConnState.DISCONNECTED)


class ScriptedCLI(ProtonCLI):
    def __init__(self, *, connect_result: CLIResult, disconnect_result: CLIResult):
        self._connect_result = connect_result
        self._disconnect_result = disconnect_result
        self.connect_calls: list[dict] = []
        self.disconnect_calls = 0

    async def connect(self, **kwargs):  # type: ignore[override]
        self.connect_calls.append(kwargs)
        return self._connect_result

    async def disconnect(self):  # type: ignore[override]
        self.disconnect_calls += 1
        return self._disconnect_result

    async def status(self):  # type: ignore[override]
        return CLIResult(0, "", "")


@pytest.fixture
def qapp():
    # QObject signals need a QCoreApplication. Reuse a single instance per session.
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


@pytest.mark.asyncio
async def test_connect_preset_emits_transitioning_then_connected(qapp, tmp_path):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[
        ConnectionInfo(state=ConnState.CONNECTED, interface="proton0", server="US-WA#1", city="Seattle", country="US"),
    ])
    store = PresetStore(path=tmp_path / "presets.json")
    seattle = store.load()[0]
    ctrl = Controller(cli, detector, poll_interval=0.05, preset_store=store)
    captured: list[ConnectionInfo] = []
    ctrl.state_changed.connect(captured.append)

    ctrl.connect_preset(seattle.id)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break

    assert cli.connect_calls == [{"city": "Seattle"}]
    states = [info.state for info in captured]
    assert ConnState.TRANSITIONING in states
    assert ConnState.CONNECTED in states


@pytest.mark.asyncio
async def test_disconnect_calls_cli(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[
        ConnectionInfo(state=ConnState.DISCONNECTED),
    ])
    ctrl = Controller(cli, detector)
    ctrl.disconnect()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break
    assert cli.disconnect_calls == 1


@pytest.mark.asyncio
async def test_overlapping_refreshes_share_one_detector_call(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = BlockingDetector()
    ctrl = Controller(cli, detector)

    first = asyncio.create_task(ctrl._refresh_state())
    await detector.started.wait()
    second = asyncio.create_task(ctrl._refresh_state())
    await asyncio.sleep(0)

    assert detector.calls == 1

    detector.release.set()
    await asyncio.gather(first, second)
    assert detector.calls == 1


def test_default_polling_is_conservative(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector)

    assert ctrl._poll_interval == POLL_INTERVAL_SECONDS
    assert ctrl._min_refresh_interval == MIN_REFRESH_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_completed_refreshes_are_rate_limited(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector, min_refresh_interval=30.0)

    await ctrl._refresh_state()
    await ctrl._refresh_state()
    await ctrl._refresh_state()

    assert detector.calls == 1


@pytest.mark.asyncio
async def test_forced_refresh_bypasses_rate_limit(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector, min_refresh_interval=30.0)

    await ctrl._refresh_state()
    await ctrl._refresh_state(force=True)

    assert detector.calls == 2


@pytest.mark.asyncio
async def test_cli_failure_surfaces_via_error_signal(qapp, tmp_path):
    cli = ScriptedCLI(
        connect_result=CLIResult(1, "", "boom: server unreachable"),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    store = PresetStore(path=tmp_path / "presets.json")
    seattle = store.load()[0]
    ctrl = Controller(cli, detector, preset_store=store)
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)
    ctrl.connect_preset(seattle.id)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break
    assert errors and "boom" in errors[0]


@pytest.mark.asyncio
async def test_disconnect_not_gated_on_auth_state(qapp):
    # disconnect should remain callable even when SIGNED_OUT — it works
    # at the CLI level and is the recovery path if detection is wrong.
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[
        ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT),
    ])
    ctrl = Controller(cli, detector)
    await ctrl._refresh_state()  # learn SIGNED_OUT

    ctrl.disconnect()
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break
    assert cli.disconnect_calls == 1


@pytest.mark.parametrize(
    "target_kind, target_value, flags, expected_kwargs",
    [
        (TargetKind.CITY, "Seattle", PresetFlags(), {"city": "Seattle"}),
        (TargetKind.CITY, "US::Seattle", PresetFlags(), {"city": "Seattle"}),
        (
            TargetKind.COUNTRY,
            "US",
            PresetFlags(p2p=True),
            {"country": "US", "p2p": True},
        ),
        (
            TargetKind.SERVER_ID,
            "US-WA#187",
            PresetFlags(),
            {"server_id": "US-WA#187"},
        ),
        (
            TargetKind.NONE,
            "",
            PresetFlags(secure_core=True),
            {"secure_core": True},
        ),
        (
            TargetKind.COUNTRY,
            "US",
            PresetFlags(tor=True, random=True),
            {"country": "US", "tor": True, "random_server": True},
        ),
    ],
)
@pytest.mark.asyncio
async def test_connect_preset_routes_to_cli(
    qapp, tmp_path, target_kind, target_value, flags, expected_kwargs
):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    store = PresetStore(path=tmp_path / "presets.json")
    store.load()
    p = store.add(
        name="case",
        target=PresetTarget(kind=target_kind, value=target_value),
        flags=flags,
    )
    ctrl = Controller(cli, detector, preset_store=store)

    ctrl.connect_preset(p.id)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break

    assert cli.connect_calls == [expected_kwargs]


@pytest.mark.asyncio
async def test_connect_preset_blocked_when_signed_out(qapp, tmp_path):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(
        answers=[ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)]
    )
    store = PresetStore(path=tmp_path / "presets.json")
    presets = store.load()
    ctrl = Controller(cli, detector, preset_store=store)
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)

    await ctrl._refresh_state()  # learn SIGNED_OUT

    ctrl.connect_preset(presets[0].id)
    await asyncio.sleep(0.02)
    assert cli.connect_calls == []
    assert errors and "sign in" in errors[0].lower()


@pytest.mark.asyncio
async def test_connect_preset_missing_id_emits_error(qapp, tmp_path):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    store = PresetStore(path=tmp_path / "presets.json")
    store.load()
    ctrl = Controller(cli, detector, preset_store=store)
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)

    ctrl.connect_preset("does-not-exist")
    await asyncio.sleep(0.02)
    assert cli.connect_calls == []
    assert errors and "not found" in errors[0].lower()


@pytest.mark.asyncio
async def test_connect_preset_without_store_emits_error(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector)  # no preset_store
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)

    ctrl.connect_preset("any-id")
    await asyncio.sleep(0.02)
    assert cli.connect_calls == []
    assert errors and "preset" in errors[0].lower()


@pytest.mark.parametrize(
    "country_code, city, expected_kwargs",
    [
        ("DE", None, {"country": "DE"}),
        ("US", "Seattle", {"city": "Seattle"}),
        ("GB", "London", {"city": "London"}),
    ],
)
@pytest.mark.asyncio
async def test_connect_to_location_routes_to_cli(qapp, country_code, city, expected_kwargs):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector)

    ctrl.connect_to_location(country_code, city)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break

    assert cli.connect_calls == [expected_kwargs]


@pytest.mark.asyncio
async def test_connect_to_location_blocked_when_signed_out(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(
        answers=[ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)]
    )
    ctrl = Controller(cli, detector)
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)
    await ctrl._refresh_state()

    ctrl.connect_to_location("DE", None)
    await asyncio.sleep(0.02)
    assert cli.connect_calls == []
    assert errors and "sign in" in errors[0].lower()


@pytest.mark.parametrize(
    "server_id, expected_kwarg",
    [
        ("US-WA#187", {"server_id": "US-WA#187"}),
        ("IT#23", {"server_id": "IT#23"}),
        ("US-GA#29-TOR", {"server_id": "US-GA#29-TOR"}),
        ("SE-US#1", {"server_id": "SE-US#1"}),
        ("us-wa#187", {"server_id": "US-WA#187"}),  # normalised to uppercase
    ],
)
@pytest.mark.asyncio
async def test_connect_to_server_id_valid(qapp, server_id, expected_kwarg):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector)

    ctrl.connect_to_server_id(server_id)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break

    assert cli.connect_calls == [expected_kwarg]


@pytest.mark.parametrize(
    "bad_id",
    ["INVALID", "US#", "US-WA-187", "US-WA#187-INVALID", "#123", ""],
)
def test_connect_to_server_id_invalid_raises(qapp, bad_id):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    ctrl = Controller(cli, detector)

    with pytest.raises(ValueError):
        ctrl.connect_to_server_id(bad_id)
    assert cli.connect_calls == []


@pytest.mark.asyncio
async def test_connect_to_server_id_blocked_when_signed_out(qapp):
    cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )
    detector = ScriptedDetector(
        answers=[ConnectionInfo(state=ConnState.DISCONNECTED, auth=AuthState.SIGNED_OUT)]
    )
    ctrl = Controller(cli, detector)
    errors: list[str] = []
    ctrl.error_occurred.connect(errors.append)
    await ctrl._refresh_state()

    ctrl.connect_to_server_id("US-WA#187")
    await asyncio.sleep(0.02)
    assert cli.connect_calls == []
    assert errors and "sign in" in errors[0].lower()


@pytest.mark.asyncio
async def test_duplicate_command_while_in_flight_is_ignored(qapp, tmp_path):
    slow_cli = ScriptedCLI(
        connect_result=CLIResult(0, "", ""),
        disconnect_result=CLIResult(0, "", ""),
    )

    async def slow_connect(**kwargs):
        slow_cli.connect_calls.append(kwargs)
        await asyncio.sleep(0.05)
        return CLIResult(0, "", "")

    slow_cli.connect = slow_connect  # type: ignore[assignment]

    detector = ScriptedDetector(answers=[ConnectionInfo(state=ConnState.DISCONNECTED)])
    store = PresetStore(path=tmp_path / "presets.json")
    seattle = store.load()[0]
    ctrl = Controller(slow_cli, detector, preset_store=store)
    ctrl.connect_preset(seattle.id)
    ctrl.connect_preset(seattle.id)  # should be ignored
    ctrl.connect_preset(seattle.id)  # ditto

    for _ in range(40):
        await asyncio.sleep(0.01)
        if ctrl._in_flight and ctrl._in_flight.done():
            break
    assert len(slow_cli.connect_calls) == 1
