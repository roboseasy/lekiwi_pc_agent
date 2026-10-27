"""calibration.py: lerobot 보정 순서를 가짜 버스 (lerobot 메서드는 진짜) 로 본다."""

import errno
import json
import os
import stat
import types
from collections.abc import Iterator
from pathlib import Path

import draccus
import pytest
from lerobot.motors import MotorCalibration

from fake_bus import LEADER_MOTORS, LEKIWI_MOTORS, FakeBus
from lekiwi_pc_agent import calibration
from lekiwi_pc_agent.calibration import CalibrationError, CalibrationSession


def move(bus: FakeBus, offsets: dict[str, int]) -> None:
	for name, delta in offsets.items():
		bus.actual[name] += delta


def recorded(session: CalibrationSession, bus: FakeBus, names: tuple[str, ...]) -> None:
	"""관절마다 -300 · +400 만큼 움직이며 sample 한다."""
	for delta in (-300, 700, -400):
		move(bus, {name: delta for name in names})
		session.sample()


def test_leader_session_follows_lerobot_order(tmp_path: Path) -> None:
	bus = FakeBus(LEADER_MOTORS)
	session = CalibrationSession(bus)
	assert session.state == 'waiting_middle'
	assert all(registers['Torque_Enable'] == 0 for registers in bus.registers.values())
	assert all(registers['Operating_Mode'] == 0 for registers in bus.registers.values())
	session.middle()
	assert session.state == 'recording'
	# 제로-포지션 위치 시키기: 지금 자세가 2047 이 되는 Homing_Offset (Present = 실제 - Homing_Offset)
	assert all(bus.present(name) == 2047 for name in LEADER_MOTORS)
	assert bus.registers['shoulder_pan']['Homing_Offset'] == 1000 - 2047
	recorded(session, bus, LEADER_MOTORS)
	joints = session.sample()
	assert joints['shoulder_pan'] == {'now': 2047, 'min': 1747, 'max': 2447}
	assert joints['wrist_roll'] == {'now': 2047, 'min': 0, 'max': 4095}
	path = session.finish(tmp_path / 'leader.json')
	assert session.state == 'done' and path == tmp_path / 'leader.json'
	with open(path) as file, draccus.config_type('json'):
		saved = draccus.load(dict[str, MotorCalibration], file)   # lerobot _load_calibration 그대로
	assert saved['shoulder_pan'] == MotorCalibration(1, 0, 1000 - 2047, 1747, 2447)
	assert saved['wrist_roll'] == MotorCalibration(5, 0, 1400 - 2047, 0, 4095)
	# 모터에도 썼다: 이제 파일과 모터가 맞는다
	assert calibration.check_files(bus, tmp_path) == {'leader': True}
	assert session.backup_path is None


def test_lekiwi_session_homes_arm_only_and_wheels_full_turn(tmp_path: Path) -> None:
	bus = FakeBus(LEKIWI_MOTORS)
	session = CalibrationSession(bus)
	assert all(registers['Torque_Enable'] == 0 for registers in bus.registers.values())
	assert bus.registers['base_left_wheel']['Operating_Mode'] == 1  # 바퀴 모드는 그대로
	wheel_offset = bus.registers['base_left_wheel']['Homing_Offset']
	session.middle()
	assert bus.registers['base_left_wheel']['Homing_Offset'] == wheel_offset
	assert bus.present('arm_elbow_flex') == 2047
	recorded(session, bus, LEKIWI_MOTORS)
	session.finish(tmp_path / 'lekiwi01.json')
	saved = json.loads((tmp_path / 'lekiwi01.json').read_text())
	assert saved['base_left_wheel'] == {
		'id': 7, 'drive_mode': 0, 'homing_offset': 0, 'range_min': 0, 'range_max': 4095}
	assert saved['arm_wrist_roll']['range_min'] == 0 and saved['arm_wrist_roll']['range_max'] == 4095
	assert saved['arm_gripper']['range_max'] - saved['arm_gripper']['range_min'] == 700
	assert ('Torque_Enable', 'arm_gripper', 1) not in bus.writes  # 토크를 켜지 않았다


def test_cancel_after_middle_restores_offsets_and_limits() -> None:
	bus = FakeBus(LEADER_MOTORS)
	before = {name: dict(registers) for name, registers in bus.registers.items()}
	session = CalibrationSession(bus)
	session.middle()
	assert bus.registers['shoulder_pan']['Min_Position_Limit'] == 0  # lerobot reset_calibration
	session.sample()
	session.cancel()
	assert session.state == 'cancelled'
	for name in LEADER_MOTORS:
		for register in ('Homing_Offset', 'Min_Position_Limit', 'Max_Position_Limit'):
			assert bus.registers[name][register] == before[name][register]


def test_cancel_before_middle_writes_no_calibration() -> None:
	bus = FakeBus(LEADER_MOTORS)
	session = CalibrationSession(bus)
	count = len(bus.writes)
	session.cancel()
	assert session.state == 'cancelled' and len(bus.writes) == count


def test_finish_refuses_unmoved_joint_and_stays_recording(tmp_path: Path) -> None:
	bus = FakeBus(LEADER_MOTORS)
	session = CalibrationSession(bus)
	session.middle()
	recorded(session, bus, LEADER_MOTORS[:3])
	with pytest.raises(CalibrationError, match='wrist_flex, gripper'):
		session.finish(tmp_path / 'leader.json')
	assert session.state == 'recording' and not (tmp_path / 'leader.json').exists()


def test_finish_backs_up_existing_file(tmp_path: Path) -> None:
	(tmp_path / 'leader.json').write_text('{"old": true}')
	bus = FakeBus(LEADER_MOTORS)
	session = CalibrationSession(bus)
	session.middle()
	recorded(session, bus, LEADER_MOTORS)
	session.finish(tmp_path / 'leader.json')
	(backup,) = tmp_path.glob('leader.json.bak-*')
	assert backup == session.backup_path
	assert backup.read_text() == '{"old": true}'
	assert len(backup.name) == len('leader.json.bak-20261022-230000')
	assert 'shoulder_pan' in json.loads((tmp_path / 'leader.json').read_text())


def test_finish_write_failure_restores_motors(tmp_path: Path) -> None:
	bus = FakeBus(LEADER_MOTORS)
	before = {name: dict(registers) for name, registers in bus.registers.items()}
	session = CalibrationSession(bus)
	session.middle()
	recorded(session, bus, LEADER_MOTORS)
	original_write = bus.write

	def failing_write(data_name: str, motor: str, value: int, **kwargs: object) -> None:
		if data_name == 'Max_Position_Limit' and motor == 'elbow_flex' and value != before[motor][data_name]:
			raise ConnectionError('There is no status packet!')
		original_write(data_name, motor, value)

	bus.write = failing_write
	with pytest.raises(ConnectionError):
		session.finish(tmp_path / 'leader.json')
	assert session.state == 'error' and not (tmp_path / 'leader.json').exists()
	assert bus.registers['shoulder_pan']['Homing_Offset'] == before['shoulder_pan']['Homing_Offset']


def test_steps_out_of_order_are_refused() -> None:
	session = CalibrationSession(FakeBus(LEADER_MOTORS))
	with pytest.raises(CalibrationError):
		session.sample()
	with pytest.raises(CalibrationError):
		session.finish(Path('/nonexistent/x.json'))
	session.middle()
	with pytest.raises(CalibrationError):
		session.middle()


def test_check_files_and_from_motors(tmp_path: Path) -> None:
	bus = FakeBus(LEADER_MOTORS)
	motors = calibration.calibration_from_motors(bus)
	assert motors['gripper'] == {
		'id': 6, 'drive_mode': 0, 'homing_offset': 105, 'range_min': 505, 'range_max': 3495}
	calibration.save_calibration_file(tmp_path / 'same.json', motors)
	other = {name: dict(value, homing_offset=value['homing_offset'] + 1) for name, value in motors.items()}
	calibration.save_calibration_file(tmp_path / 'other.json', other)
	(tmp_path / 'broken.json').write_text('{')
	(tmp_path / 'notes.txt').write_text('x')
	count = len(bus.writes)
	assert calibration.check_files(bus, tmp_path) == {'broken': False, 'other': False, 'same': True}
	assert len(bus.writes) == count  # 읽기만


def test_is_valid_id() -> None:
	assert calibration.is_valid_id('leader_01-a')
	for bad in ('', 'a' * 41, '../x', 'a b', 'a.json', None, 3):
		assert not calibration.is_valid_id(bad)


@pytest.fixture
def pty_slave() -> Iterator[tuple[int, str]]:
	"""(가짜 시리얼 fd, 그 장치 경로): pty 의 slave 쪽."""
	master, slave = os.openpty()
	yield slave, os.ttyname(slave)
	os.close(slave)
	os.close(master)


def assert_exclusive(device: str) -> None:
	"""TIOCEXCL 이 걸렸으면 같은 장치를 또 열 때 EBUSY (root 가 아닐 때)."""
	with pytest.raises(OSError) as raised:
		os.close(os.open(device, os.O_RDWR | os.O_NOCTTY))
	assert raised.value.errno == errno.EBUSY


def test_open_bus_never_configures_and_locks_port(
		monkeypatch: pytest.MonkeyPatch, pty_slave: tuple[int, str]) -> None:
	from lerobot.motors.feetech import FeetechMotorsBus
	descriptor, device = pty_slave
	connected: list[FeetechMotorsBus] = []

	def fake_connect(self: FeetechMotorsBus, handshake: bool = True) -> None:
		self.port_handler.ser = types.SimpleNamespace(fileno=lambda: descriptor)
		connected.append(self)

	monkeypatch.setattr(FeetechMotorsBus, 'connect', fake_connect)
	os.close(os.open(device, os.O_RDWR | os.O_NOCTTY))  # 걸기 전에는 열린다
	leader_bus = calibration.open_bus(calibration.DEVICE_SO101_LEADER, '/dev/ttyACM0')
	assert list(leader_bus.motors) == list(LEADER_MOTORS)
	assert connected == [leader_bus]
	assert_exclusive(device)
	with pytest.raises(ValueError):
		calibration.open_bus('nope', '/dev/ttyACM0')


@pytest.mark.parametrize('device_type', [calibration.DEVICE_SO101_LEADER, calibration.DEVICE_LEKIWI])
def test_open_bus_stays_connected_after_the_device_is_collected(
		monkeypatch: pytest.MonkeyPatch, pty_slave: tuple[int, str], device_type: str) -> None:
	"""lerobot 장치의 __del__ 은 연결돼 있으면 disconnect() 한다: open_bus 가 돌아온 뒤에도 버스가 열려 있어야 한다
	(2026-10-24 실물: 돌아오자마자 포트가 닫혀 'FeetechMotorsBus is not connected')."""
	import gc

	from lerobot.motors.feetech import FeetechMotorsBus
	descriptor, _device = pty_slave
	closed: list[bool] = []

	def fake_connect(self: FeetechMotorsBus, handshake: bool = True) -> None:
		self.port_handler.ser = types.SimpleNamespace(fileno=lambda: descriptor)
		self.port_handler.is_open = True

	monkeypatch.setattr(FeetechMotorsBus, 'connect', fake_connect)
	monkeypatch.setattr(FeetechMotorsBus, 'disconnect', lambda self, disable_torque=True: closed.append(disable_torque))
	bus = calibration.open_bus(device_type, '/dev/ttyACM0')
	gc.collect()
	assert bus.is_connected and closed == []


def test_open_bus_closes_when_lock_fails(monkeypatch: pytest.MonkeyPatch) -> None:
	from lerobot.motors.feetech import FeetechMotorsBus
	read_end, write_end = os.pipe()  # tty 가 아니라 TIOCEXCL 이 실패한다
	closed: list[bool] = []
	monkeypatch.setattr(FeetechMotorsBus, 'connect', lambda self, handshake=True: setattr(
		self.port_handler, 'ser', types.SimpleNamespace(fileno=lambda: read_end)))
	monkeypatch.setattr(
		FeetechMotorsBus, 'disconnect', lambda self, disable_torque=True: closed.append(disable_torque))
	try:
		with pytest.raises(OSError):
			calibration.open_bus(calibration.DEVICE_SO101_LEADER, '/dev/ttyACM0')
	finally:
		os.close(read_end)
		os.close(write_end)
	assert closed == [False]


def test_session_refuses_when_arm_torque_is_on() -> None:
	bus = FakeBus(LEKIWI_MOTORS)
	bus.registers['arm_elbow_flex']['Torque_Enable'] = 1
	with pytest.raises(CalibrationError, match='팔 토크가 켜져 있습니다 \\(arm_elbow_flex\\)'):
		CalibrationSession(bus)
	assert bus.writes == []  # 토크를 끄지도 않았다 (잡고 있던 팔이 떨어지지 않는다)


def test_save_keeps_file_present_and_fsyncs(
		monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	path = tmp_path / 'leader.json'
	path.write_text('{"old": true}')
	data = calibration.calibration_from_motors(FakeBus(LEADER_MOTORS))
	fsynced: list[int] = []
	real_fsync, real_replace = os.fsync, os.replace

	def record_fsync(descriptor: int) -> None:
		fsynced.append(descriptor)
		real_fsync(descriptor)

	def checked_replace(source: str, target: object) -> None:
		assert path.read_text() == '{"old": true}'  # 바꿔 끼우기 직전까지 예전 파일이 그대로 있다
		assert len(fsynced) >= 2  # 임시 파일 · 백업을 이미 fsync 했다
		real_replace(source, target)

	monkeypatch.setattr(os, 'fsync', record_fsync)
	monkeypatch.setattr(os, 'replace', checked_replace)
	backup = calibration.save_calibration_file(path, data)
	assert len(fsynced) == 3  # 임시 파일 · 백업 · 폴더
	assert backup.read_text() == '{"old": true}'
	assert calibration.load_calibration_file(path)['gripper'].range_max == 3495
	assert stat.S_IMODE(path.stat().st_mode) == 0o644
	assert sorted(entry.name for entry in tmp_path.iterdir()) == ['leader.json', backup.name]


def test_two_saves_in_one_second_keep_both_backups(
		monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	monkeypatch.setattr(calibration.time, 'strftime', lambda _format: '20261022-231500')
	path = tmp_path / 'leader.json'
	path.write_text('1')
	data = calibration.calibration_from_motors(FakeBus(LEADER_MOTORS))
	first = calibration.save_calibration_file(path, data)
	path.write_text('2')
	second = calibration.save_calibration_file(path, data)
	assert first.name == 'leader.json.bak-20261022-231500'
	assert second.name == 'leader.json.bak-20261022-231500-1'
	assert (first.read_text(), second.read_text()) == ('1', '2')


def test_save_failure_leaves_original_and_no_temp(
		monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	path = tmp_path / 'leader.json'
	path.write_text('{"old": true}')

	def broken_dump(*args: object, **kwargs: object) -> None:
		raise OSError('디스크가 가득 찼습니다')

	monkeypatch.setattr(calibration.draccus, 'dump', broken_dump)
	with pytest.raises(OSError):
		calibration.save_calibration_file(path, calibration.calibration_from_motors(FakeBus(LEADER_MOTORS)))
	assert [entry.name for entry in tmp_path.iterdir()] == ['leader.json']
	assert path.read_text() == '{"old": true}'


def test_ids_skip_bad_names_and_cap_at_32(tmp_path: Path) -> None:
	data = calibration.calibration_from_motors(FakeBus(LEADER_MOTORS))
	for name in ('leader.v2', 'a b', 'x' * 41):
		(tmp_path / f'{name}.json').write_text('{}')
	for index in range(40):
		calibration.save_calibration_file(tmp_path / f'id{index:02d}.json', data)
	ids = calibration.list_calibration_ids(tmp_path)
	assert ids == [f'id{index:02d}' for index in range(32)]
	matches = calibration.check_files(FakeBus(LEADER_MOTORS), tmp_path)
	assert list(matches) == ids and all(matches.values())
