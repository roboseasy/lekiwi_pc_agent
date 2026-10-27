"""leader.py: 포트 · 보정 찾기, LerobotLeader (lerobot SO101Leader 를 가짜로 바꿔 끼운다)."""

import errno
import os
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from lekiwi_pc_agent import leader
from lekiwi_pc_agent.leader import LeaderError, LerobotLeader


class FakeSoLeader:
	"""lerobot SO101Leader 흉내. is_calibrated 로 보정 일치 여부를 정한다."""

	is_calibrated_value = True
	instances: list['FakeSoLeader'] = []

	descriptor = -1   # 가짜 시리얼 fd (pty)

	def __init__(self, config: object) -> None:
		self.config = config
		self.is_connected = False
		self.bus = types.SimpleNamespace(port_handler=types.SimpleNamespace(
			ser=types.SimpleNamespace(fileno=lambda: FakeSoLeader.descriptor)))
		self.calibrate_flags: list[bool] = []
		FakeSoLeader.instances.append(self)

	@property
	def is_calibrated(self) -> bool:
		return self.is_calibrated_value

	def connect(self, calibrate: bool = True) -> None:
		self.calibrate_flags.append(calibrate)
		self.is_connected = True

	def get_action(self) -> dict[str, float]:
		return {'shoulder_pan.pos': 12, 'gripper.pos': 3.5}

	def disconnect(self) -> None:
		self.is_connected = False


@pytest.fixture
def fake_lerobot(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeSoLeader]]:
	so_leader = pytest.importorskip('lerobot.teleoperators.so_leader')
	monkeypatch.setattr(so_leader, 'SO101Leader', FakeSoLeader)
	FakeSoLeader.instances = []
	FakeSoLeader.is_calibrated_value = True
	master, slave = os.openpty()
	FakeSoLeader.descriptor = slave
	yield FakeSoLeader
	os.close(slave)
	os.close(master)


def test_lerobot_leader_never_calibrates_and_adds_arm_prefix(
		fake_lerobot: type[FakeSoLeader], tmp_path: Path) -> None:
	opened = LerobotLeader('/dev/ttyACM0', 'leader', tmp_path)
	(fake,) = fake_lerobot.instances
	assert fake.calibrate_flags == [False]
	assert fake.config.port == '/dev/ttyACM0' and fake.config.id == 'leader'
	assert fake.config.calibration_dir == tmp_path and fake.config.use_degrees
	# 포트를 독점했다: 뒤에 같은 포트를 여는 프로그램은 EBUSY
	with pytest.raises(OSError) as raised:
		os.close(os.open(os.ttyname(fake.descriptor), os.O_RDWR | os.O_NOCTTY))
	assert raised.value.errno == errno.EBUSY
	joints = opened.read()
	assert joints == {'arm_shoulder_pan.pos': 12.0, 'arm_gripper.pos': 3.5}
	assert all(type(value) is float for value in joints.values())
	opened.close()
	opened.close()
	assert not fake.is_connected


def test_lerobot_leader_mismatched_calibration_is_error_and_closes(
		fake_lerobot: type[FakeSoLeader], tmp_path: Path) -> None:
	fake_lerobot.is_calibrated_value = False
	with pytest.raises(LeaderError, match='모터 값과 다릅니다'):
		LerobotLeader('/dev/ttyACM0', 'leader', tmp_path)
	assert not fake_lerobot.instances[0].is_connected


def test_list_ports_pairs_acm_with_by_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
	(tmp_path / 'ttyACM0').touch()
	(tmp_path / 'ttyACM1').touch()
	(tmp_path / 'ttyUSB0').touch()
	by_id = tmp_path / 'by-id'
	by_id.mkdir()
	(by_id / 'usb-leader-if00').symlink_to(tmp_path / 'ttyACM1')
	(by_id / 'usb-other-if00').symlink_to(tmp_path / 'ttyUSB0')
	monkeypatch.setattr(leader, 'ACM_GLOB', str(tmp_path / 'ttyACM*'))
	monkeypatch.setattr(leader, 'BY_ID_GLOB', str(by_id / '*'))
	assert leader.list_ports() == [
		{'path': str(tmp_path / 'ttyACM0'), 'by_id': None},
		{'path': str(tmp_path / 'ttyACM1'), 'by_id': str(by_id / 'usb-leader-if00')}]


def test_describe_error_adds_hint() -> None:
	reason = leader.describe_error(
		'리더암을 열지 못했습니다', OSError("[Errno 13] could not open port: Permission denied"))
	assert reason.startswith('리더암을 열지 못했습니다: 포트 권한이 없습니다')
	assert leader.describe_error('x', LeaderError('그대로')) == '그대로'


def test_describe_error_reads_wrapped_serial_error() -> None:
	try:
		try:
			raise OSError("[Errno 16] could not open port /dev/ttyACM0: Device or resource busy")
		except OSError as cause:
			raise ConnectionError("\nCould not connect on port '/dev/ttyACM0'.\n") from cause
	except ConnectionError as error:
		reason = leader.describe_error('리더암을 열지 못했습니다', error)
	assert '다른 프로그램이 포트를 쓰고 있습니다' in reason and '\n' not in reason
