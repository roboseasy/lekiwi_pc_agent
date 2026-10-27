"""시험용 가짜 Feetech 버스: 레지스터만 흉내 내고 lerobot 의 보정 메서드는 그대로 돈다.

FeetechMotorsBus 를 상속해 read · write · sync_read (와 열림 여부) 만 바꾼다. 그래서
set_half_turn_homings · reset_calibration · read/write_calibration · disable_torque 는 lerobot 코드다.
Feetech 처럼 Present_Position = 실제 위치 - Homing_Offset (0~4095 로 감김).
"""

from lerobot.motors import Motor, MotorNormMode
from lerobot.motors.feetech import FeetechMotorsBus

LEADER_MOTORS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
LEKIWI_MOTORS = tuple(f'arm_{name}' for name in LEADER_MOTORS) + (
	'base_left_wheel', 'base_back_wheel', 'base_right_wheel')


class FakeBus(FeetechMotorsBus):
	"""레지스터 dict 를 가진 가짜 버스. writes 에 (레지스터, 모터, 값) 을 남긴다."""

	def __init__(self, names: tuple[str, ...]) -> None:
		super().__init__('fake', {
			name: Motor(index + 1, 'sts3215', MotorNormMode.DEGREES) for index, name in enumerate(names)})
		self.registers = {
			name: {'Homing_Offset': 100 + index, 'Min_Position_Limit': 500 + index,
				'Max_Position_Limit': 3500 - index, 'Lock': 1, 'Operating_Mode': 1,
				'Torque_Enable': int('wheel' in name)}  # 팔 힘 뺌, 바퀴는 켬 (보정을 막지 않아야 한다)
			for index, name in enumerate(names)}
		self.actual = {name: 1000 + 100 * index for index, name in enumerate(names)}
		self.writes: list[tuple[str, str, int]] = []
		self.is_open = True
		self.fail_on: str | None = None   # 이 레지스터를 쓰면 ConnectionError

	@property
	def is_connected(self) -> bool:
		return self.is_open

	def present(self, name: str) -> int:
		return (self.actual[name] - self.registers[name]['Homing_Offset']) % 4096

	def read(self, data_name: str, motor: str, *, normalize: bool = True, num_retry: int = 0) -> int:
		if data_name == 'Present_Position':
			return self.present(motor)
		return self.registers[motor][data_name]

	def write(
			self, data_name: str, motor: str, value: int, *, normalize: bool = True,
			num_retry: int = 0) -> None:
		if data_name == self.fail_on:
			raise ConnectionError('There is no status packet!')
		self.writes.append((data_name, motor, int(value)))
		self.registers[motor][data_name] = int(value)

	def sync_read(
			self, data_name: str, motors: str | list[str] | None = None, *, normalize: bool = True,
			num_retry: int = 0) -> dict[str, int]:
		return {name: self.read(data_name, name) for name in self._get_motors_list(motors)}

	def disconnect(self, disable_torque: bool = True) -> None:
		self.is_open = False
