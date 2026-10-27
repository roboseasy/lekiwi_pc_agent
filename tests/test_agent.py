"""agent.py · leader.py: 가짜 게이트웨이 + 가짜 리더암으로 규약 (docs/agent_protocol.md v1) 을 본다."""

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from fake_bus import LEADER_MOTORS, FakeBus
from fake_gateway import JOINT_KEYS, FakeGateway, FakeLeader
from lekiwi_pc_agent import agent as agent_module
from lekiwi_pc_agent import calibration
from lekiwi_pc_agent.agent import Agent

PC_AGENT_DIR = Path(__file__).resolve().parent.parent
BY_ID = '/dev/serial/by-id/usb-fake_leader-if00'


class Harness:
	"""스레드에서 도는 Agent 하나와 그 가짜들."""

	def __init__(self, gateway: FakeGateway, calibration_dir: Path, leader: FakeLeader) -> None:
		self.gateway = gateway
		self.leader = leader
		self.opened: list[tuple[str, str]] = []
		self.buses: list[FakeBus] = []
		self.ports = [{'path': '/dev/ttyACM0', 'by_id': BY_ID}]
		self.stop_event = threading.Event()
		self.agent = Agent(
			'127.0.0.1', gateway.port, self._open, self._open_bus, calibration_dir,
			port_lister=lambda: [dict(entry) for entry in self.ports], hostname='test-pc')
		self.thread = threading.Thread(target=self.agent.run, args=(self.stop_event,), daemon=True)
		self.thread.start()

	def _open(self, port: str, calibration_id: str) -> FakeLeader:
		self.opened.append((port, calibration_id))
		return self.leader

	def _open_bus(self, port: str) -> FakeBus:
		bus = FakeBus(LEADER_MOTORS)
		bus.port_name = port
		self.buses.append(bus)
		return bus

	def calib_states(self) -> list[str]:
		return [message['state'] for message in self.gateway.messages('calib_status')]

	def last_calib(self) -> dict:
		return self.gateway.messages('calib_status')[-1]

	def calib(self, command: str, **fields: str) -> None:
		self.gateway.send({'type': f'calib_{command}', **fields})

	def statuses(self) -> list[str]:
		return [message['state'] for message in self.gateway.messages('leader_status')]

	def connect_leader(self, port: str = BY_ID, calibration_id: str = 'leader') -> None:
		self.gateway.send({'type': 'leader_connect', 'port': port, 'calibration_id': calibration_id})

	def stop(self) -> None:
		self.stop_event.set()
		self.thread.join(5.0)
		assert not self.thread.is_alive()


@pytest.fixture
def gateway() -> Iterator[FakeGateway]:
	fake = FakeGateway()
	yield fake
	fake.stop()


@pytest.fixture
def calibration_dir(tmp_path: Path) -> Path:
	for name in ('leader01', 'leader'):
		(tmp_path / f'{name}.json').write_text('{}')
	(tmp_path / 'notes.txt').write_text('보정 파일이 아님')
	return tmp_path


@pytest.fixture
def harness(gateway: FakeGateway, calibration_dir: Path) -> Iterator[Harness]:
	running = Harness(gateway, calibration_dir, FakeLeader())
	yield running
	running.stop()


def welcomed(harness: Harness) -> None:
	"""hello → welcome 이 끝나고 (첫 leader_status 를 받은 뒤) 까지."""
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader_status'))


def test_hello_lists_ports_calibration_ids_and_no_token(harness: Harness) -> None:
	harness.gateway.wait_for(lambda: harness.gateway.messages('agent_hello'))
	(hello,) = harness.gateway.messages('agent_hello')
	assert hello['version'] == 2
	assert hello['host'] == 'test-pc'
	assert hello['ports'] == [{'path': '/dev/ttyACM0', 'by_id': BY_ID}]
	assert hello['calibration_ids'] == ['leader', 'leader01']
	assert hello['lerobot_version'] == agent_module._lerobot_version()
	assert 'token' not in hello
	# welcome 뒤에 지금 리더 상태를 한 번 알린다
	welcomed(harness)
	assert harness.statuses() == ['disconnected']


def test_heartbeat_every_half_second(harness: Harness) -> None:
	harness.gateway.wait_for(lambda: len(harness.gateway.times('heartbeat')) >= 5)
	stamps = harness.gateway.times('heartbeat')
	gaps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
	assert all(0.4 < gap < 0.6 for gap in gaps), gaps


def test_leader_connect_streams_30hz_with_arm_keys(harness: Harness) -> None:
	welcomed(harness)
	harness.connect_leader()
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader'))
	time.sleep(1.0)
	frames = harness.gateway.messages('leader')
	stamps = harness.gateway.times('leader')
	rate = (len(stamps) - 1) / (stamps[-1] - stamps[0])
	assert 27.0 < rate < 33.0, rate
	assert all(set(frame['joints']) == JOINT_KEYS for frame in frames)
	assert [frame['seq'] for frame in frames] == list(range(1, len(frames) + 1))
	assert harness.opened == [(BY_ID, 'leader')]
	assert harness.statuses() == ['disconnected', 'connecting', 'connected']
	connected = harness.gateway.messages('leader_status')[-1]
	assert connected['port'] == BY_ID and connected['calibration_id'] == 'leader'


def test_leader_disconnect_closes_leader_and_stops_values(harness: Harness) -> None:
	welcomed(harness)
	harness.connect_leader('/dev/ttyACM0')
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader'))
	harness.gateway.send({'type': 'leader_disconnect'})
	harness.gateway.wait_for(lambda: harness.statuses()[-1] == 'disconnected')
	assert harness.leader.is_closed
	count = len(harness.gateway.messages('leader'))
	time.sleep(0.3)
	assert len(harness.gateway.messages('leader')) == count


def test_read_error_reports_error_and_stops(gateway: FakeGateway, calibration_dir: Path) -> None:
	harness = Harness(gateway, calibration_dir, FakeLeader(fail_after=10))
	try:
		welcomed(harness)
		harness.connect_leader()
		gateway.wait_for(lambda: harness.statuses()[-1] == 'error')
		status = gateway.messages('leader_status')[-1]
		assert '리더암을 읽지 못했습니다' in status['reason']
		assert '모터가 답하지 않습니다' in status['reason']
		assert len(gateway.messages('leader')) == 10
		assert harness.leader.is_closed
		# 오류 뒤 '리더암 끊기' 는 오류를 지운다
		gateway.send({'type': 'leader_disconnect'})
		gateway.wait_for(lambda: harness.statuses()[-1] == 'disconnected')
	finally:
		harness.stop()


def test_leader_connect_only_opens_listed_port_and_calibration(harness: Harness) -> None:
	welcomed(harness)
	harness.connect_leader('/dev/ttyUSB0')
	harness.gateway.wait_for(lambda: harness.statuses()[-1] == 'error')
	assert '이 PC 에 없는 리더 포트' in harness.gateway.messages('leader_status')[-1]['reason']
	harness.connect_leader(calibration_id='../../etc/passwd')
	harness.gateway.wait_for(lambda: len(harness.statuses()) >= 5)
	assert harness.statuses()[-1] == 'error'
	assert '보정 파일이 없습니다' in harness.gateway.messages('leader_status')[-1]['reason']
	assert harness.opened == []


def test_ports_change_sends_ports(harness: Harness) -> None:
	welcomed(harness)
	harness.ports.append({'path': '/dev/ttyACM1', 'by_id': None})
	harness.gateway.wait_for(lambda: harness.gateway.messages('ports'), timeout=3.0)
	(ports,) = harness.gateway.messages('ports')
	assert ports['ports'] == harness.ports


def test_reconnect_keeps_token_and_resumes_leader(harness: Harness) -> None:
	welcomed(harness)
	harness.connect_leader()
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader'))
	harness.gateway.drop()
	harness.gateway.wait_for(lambda: len(harness.gateway.messages('agent_hello')) == 2)
	first, second = harness.gateway.messages('agent_hello')
	assert 'token' not in first and second['token'] == 'token-1'
	# 리더는 열어 둔 채라 다시 붙으면 connected 를 다시 알리고 값을 이어 보낸다
	harness.gateway.wait_for(lambda: harness.statuses()[-1] == 'connected'
		and len(harness.statuses()) == 4)
	count = len(harness.gateway.messages('leader'))
	harness.gateway.wait_for(lambda: len(harness.gateway.messages('leader')) > count + 5)
	assert harness.opened == [(BY_ID, 'leader')]
	seqs = [frame['seq'] for frame in harness.gateway.messages('leader')]
	assert seqs == sorted(set(seqs))


def test_stop_closes_leader_then_websocket(harness: Harness) -> None:
	welcomed(harness)
	harness.connect_leader()
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader'))
	harness.stop()
	assert harness.leader.is_closed
	assert harness.statuses()[-1] == 'disconnected'  # 연결이 열려 있을 때 알렸다
	harness.gateway.wait_for(lambda: harness.gateway.closed_count == 1)


@pytest.mark.parametrize('signal_number', [signal.SIGINT, signal.SIGTERM])
def test_signal_exits_zero_without_traceback(gateway: FakeGateway, signal_number: int) -> None:
	env = {key: value for key, value in os.environ.items()
		if key not in ('PYTHONPATH', 'AMENT_PREFIX_PATH')}
	process = subprocess.Popen(
		[sys.executable, '-m', 'lekiwi_pc_agent', '--robot', '127.0.0.1', '--port', str(gateway.port)],
		cwd=PC_AGENT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
	try:
		gateway.wait_for(lambda: gateway.messages('heartbeat'), timeout=15.0)
		process.send_signal(signal_number)
		_, stderr = process.communicate(timeout=10.0)
	finally:
		process.kill()
	assert process.returncode == 0, stderr
	assert 'Traceback' not in stderr
	assert '게이트웨이에 연결됐습니다' in stderr and 'PC 에이전트를 끝냈습니다' in stderr
	gateway.wait_for(lambda: gateway.closed_count == 1)


# ── 규약 v2: 보정 ─────────────────────────────────────────


def wait_calib(harness: Harness, state: str) -> dict:
	harness.gateway.wait_for(lambda: harness.calib_states() and harness.calib_states()[-1] == state)
	return harness.last_calib()


def test_welcome_sends_calib_status(harness: Harness) -> None:
	welcomed(harness)
	harness.gateway.wait_for(lambda: harness.gateway.messages('calib_status'))
	assert harness.last_calib() == {
		'type': 'calib_status', 'state': 'idle', 'calibration_id': None, 'matches': None,
		'joints': None, 'reason': None}


def test_calib_check_reports_matches_and_closes_bus(harness: Harness, calibration_dir: Path) -> None:
	calibration.save_calibration_file(
		calibration_dir / 'match.json', calibration.calibration_from_motors(FakeBus(LEADER_MOTORS)))
	welcomed(harness)
	harness.calib('check')
	harness.gateway.wait_for(lambda: harness.last_calib()['matches'] is not None)
	status = harness.last_calib()
	assert status['state'] == 'idle'
	assert status['matches'] == {'leader': False, 'leader01': False, 'match': True}
	(bus,) = harness.buses
	assert bus.port_name == '/dev/ttyACM0' and not bus.is_open  # 하나뿐인 포트를 열고 닫았다
	assert not any(name == 'Torque_Enable' for name, _, _ in bus.writes)  # 읽기만


def test_calib_session_releases_leader_records_and_saves(
		harness: Harness, calibration_dir: Path) -> None:
	welcomed(harness)
	harness.connect_leader()
	harness.gateway.wait_for(lambda: harness.gateway.messages('leader'))
	harness.calib('start', calibration_id='new01')
	wait_calib(harness, 'waiting_middle')
	assert harness.leader.is_closed and harness.statuses()[-1] == 'disconnected'
	(bus,) = harness.buses
	assert bus.port_name == BY_ID  # 마지막 리더 포트
	leader_count = len(harness.gateway.messages('leader'))
	harness.connect_leader()  # 보정 중에는 리더를 열지 않는다
	harness.gateway.wait_for(lambda: harness.statuses()[-1] == 'error')
	assert '보정 중' in harness.gateway.messages('leader_status')[-1]['reason']
	harness.calib('middle')
	wait_calib(harness, 'recording')
	for delta in (-300, 700, -400):
		for name in LEADER_MOTORS:
			bus.actual[name] += delta
		time.sleep(0.2)
	time.sleep(1.0)
	stamps = [stamp for stamp, message in harness.gateway.received
		if message['type'] == 'calib_status' and message['state'] == 'recording']
	rate = (len(stamps) - 1) / (stamps[-1] - stamps[0])
	assert 8.0 < rate < 12.0, rate
	joints = harness.last_calib()['joints']
	assert joints['shoulder_pan'] == {'now': 2047, 'min': 1747, 'max': 2447}
	harness.calib('finish')
	status = wait_calib(harness, 'done')
	assert status['calibration_id'] == 'new01' and status['matches']['new01'] is True
	assert (calibration_dir / 'new01.json').exists() and not bus.is_open
	harness.gateway.wait_for(lambda: harness.gateway.messages('calibration_ids'))
	assert harness.gateway.messages('calibration_ids')[-1]['calibration_ids'] == [
		'leader', 'leader01', 'new01']
	assert len(harness.gateway.messages('leader')) == leader_count


def test_calib_cancel_restores_motors_and_writes_no_file(
		harness: Harness, calibration_dir: Path) -> None:
	welcomed(harness)
	harness.calib('start', calibration_id='leader')
	wait_calib(harness, 'waiting_middle')
	(bus,) = harness.buses
	before = {name: dict(registers) for name, registers in bus.registers.items()}
	harness.calib('middle')
	wait_calib(harness, 'recording')
	harness.calib('cancel')
	status = wait_calib(harness, 'cancelled')
	assert status['calibration_id'] == 'leader' and not bus.is_open
	for name in LEADER_MOTORS:
		for register in ('Homing_Offset', 'Min_Position_Limit', 'Max_Position_Limit'):
			assert bus.registers[name][register] == before[name][register]
	assert (calibration_dir / 'leader.json').read_text() == '{}'
	assert not list(calibration_dir.glob('*.bak-*'))


def test_calib_finish_unmoved_stays_recording(harness: Harness) -> None:
	welcomed(harness)
	harness.calib('start', calibration_id='new02')
	wait_calib(harness, 'waiting_middle')
	harness.calib('middle')
	wait_calib(harness, 'recording')
	harness.calib('finish')
	harness.gateway.wait_for(lambda: harness.last_calib()['reason'])
	assert harness.last_calib()['state'] == 'recording'
	assert '움직이지 않은 관절' in harness.last_calib()['reason']
	assert harness.buses[0].is_open


def test_calib_from_motors_refuses_existing_and_bad_names(
		harness: Harness, calibration_dir: Path) -> None:
	welcomed(harness)
	harness.calib('from_motors', calibration_id='leader')
	assert '이미 있는' in wait_calib(harness, 'error')['reason']
	harness.calib('from_motors', calibration_id='../escape')
	harness.gateway.wait_for(lambda: '영문' in (harness.last_calib()['reason'] or ''))
	assert harness.last_calib()['calibration_id'] is None
	assert harness.buses == []  # 거절은 버스를 열기 전에
	harness.calib('from_motors', calibration_id='fresh')
	status = wait_calib(harness, 'done')
	assert status['matches']['fresh'] is True
	assert calibration.load_calibration_file(calibration_dir / 'fresh.json')['gripper'].range_max == 3495
	harness.gateway.wait_for(lambda: harness.gateway.messages('calibration_ids'))


def test_stop_during_calibration_cancels_and_restores(harness: Harness) -> None:
	welcomed(harness)
	harness.calib('start', calibration_id='new03')
	wait_calib(harness, 'waiting_middle')
	(bus,) = harness.buses
	offset = bus.registers['elbow_flex']['Homing_Offset']
	harness.calib('middle')
	wait_calib(harness, 'recording')
	harness.stop()
	assert bus.registers['elbow_flex']['Homing_Offset'] == offset and not bus.is_open
	assert harness.calib_states()[-1] == 'cancelled'  # 연결이 열려 있을 때 알렸다


def test_calib_start_refused_when_arm_torque_on(harness: Harness) -> None:
	welcomed(harness)
	original = harness._open_bus

	def torque_on_bus(port: str) -> FakeBus:
		bus = original(port)
		bus.registers['wrist_flex']['Torque_Enable'] = 1
		return bus

	harness.agent._open_bus = torque_on_bus
	harness.calib('start', calibration_id='new04')
	status = wait_calib(harness, 'error')
	assert '팔 토크가 켜져 있습니다' in status['reason']
	(bus,) = harness.buses
	assert not bus.is_open and bus.writes == []


def test_calib_refuses_bus_while_leader_still_holds_port(
		harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setattr(agent_module, 'LEADER_CLOSE_TIMEOUT', 0.2)
	release = threading.Event()

	def slow_open(port: str, calibration_id: str) -> FakeLeader:
		release.wait(5.0)  # lerobot connect 가 오래 걸리는 것처럼
		return harness.leader

	harness.agent._open = slow_open
	welcomed(harness)
	harness.connect_leader()
	harness.gateway.wait_for(lambda: harness.statuses()[-1] == 'connecting')
	harness.calib('check')
	status = wait_calib(harness, 'error')
	assert '포트를 놓지 않았습니다' in status['reason']
	assert harness.buses == []  # 버스를 또 열지 않았다
	release.set()


def test_calib_finish_done_even_if_recheck_fails(
		harness: Harness, calibration_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	welcomed(harness)
	harness.calib('start', calibration_id='new05')
	wait_calib(harness, 'waiting_middle')
	(bus,) = harness.buses
	harness.calib('middle')
	wait_calib(harness, 'recording')
	for delta in (-300, 700):
		for name in LEADER_MOTORS:
			bus.actual[name] += delta
		time.sleep(0.1)

	def broken_check(*args: object) -> dict:
		raise ConnectionError('There is no status packet!')

	monkeypatch.setattr(calibration, 'check_files', broken_check)
	harness.calib('finish')
	status = wait_calib(harness, 'done')
	assert status['matches'] is None and status['reason'] is None
	assert (calibration_dir / 'new05.json').exists() and not bus.is_open
	assert 'error' not in harness.calib_states()
