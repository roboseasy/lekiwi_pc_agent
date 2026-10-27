# 캘리브레이션 탭 (2026-10-22, 사용자 요청)

왼쪽 메뉴의 대시보드와 조작 사이에 '캘리브레이션'(`#/calibration`)을 둔다. 팔로워(르키위 팔, 로봇)와 리더(SO-101, 노트북)의 lerobot 보정을 확인하고 고르고 새로 만든다.

## 왜

2026-10-22 실물에서 두 팔 모두 쓰려던 보정 파일이 모터와 맞지 않았다.

- 리더: 노트북의 `leader` · `leader01` · `leader02` · `leader_lekiwi` 모두 모터와 달랐다. 모터 값으로 `leader.json`을 다시 만들었다.
- 팔로워: `lekiwi04.json`이 모터와 달랐다. `lekiwi01.json`(10-16)이 맞아 `~/lekiwi_ws/robot_profile.yaml`에 `robot_id: lekiwi01`을 넣었다.

모터 EEPROM(Homing_Offset · Min/Max_Position_Limit)에는 마지막 보정이 남아 있다. lerobot `is_calibrated`도 이 값으로 보정 파일과 모터를 대조한다.

## 화면

| 부분 | 팔로워 (로봇) | 리더 (노트북, PC 에이전트) |
|------|------|------|
| 지금 쓰는 보정 | `status.teleop_arm.follower.robot_id`. 모터와 맞는지 표시 | 에이전트 `leader.calibration_id` |
| 파일 목록 | 로봇 `~/.cache/huggingface/lerobot/calibration/robots/lekiwi/*.json`. 파일마다 모터와 맞음 · 다름 | 노트북 `…/teleoperators/so_leader/*.json`. '모터와 맞춰 보기'를 누르면 파일마다 맞음 · 다름 |
| 고르기 | 맞는 파일을 골라 쓰기. 게이트웨이 전용 `~/lekiwi_ws/arm_profile.yaml`의 `robot_id`를 바꾸고 로봇 기능을 다시 켠다 | 맞는 파일 이름을 조작 화면의 보정 선택 기본값으로 |
| 모터 값으로 파일 만들기 | 모터 EEPROM 값으로 `<이름>.json`을 만든다 (새 이름만. 덮어쓰지 않는다) | 같음 |
| 새로 보정 | 아래 안내 순서 | 같음 |
| 접은 자세 저장 | 지금 팔 자세(토크 끔)를 `rest_pose_ticks`로 저장 | — |

새로 보정하면 예전 데이터셋과 값이 달라질 수 있다. 화면에 이렇게 경고한다: "새로 보정하면 같은 자세라도 값이 달라질 수 있습니다. 예전 데이터셋과 섞어 학습할 때 주의하세요".

## 새로 보정 순서 (lerobot과 같게)

1. **준비**
   - 팔로워: 팔 토크가 꺼져 있어야 한다 (팔 세션 없음, `is_torque_maybe_on` false). 게이트웨이가 로봇 기능을 끄고, 버스를 쓰는 보정 프로그램을 lerobot 가상환경으로 켠다.
   - 리더: 리더암 연결을 끊고, 에이전트가 버스를 연다.
2. **가운데 자세**: "팔을 모든 관절 범위의 가운데로 옮긴 뒤 '제로-포지션 위치 시키기'를 누르세요". 이때 lerobot `set_half_turn_homings`로 Homing_Offset을 쓴다.
3. **범위 재기**: "모든 관절을 한 번씩 끝까지 천천히 움직이세요 (wrist_roll 제외). 다 했으면 '끝내기'". 화면은 관절마다 지금 · 최소 · 최대 tick을 실시간으로 보여 준다. wrist_roll(한 바퀴)과 바퀴는 0~4095로 둔다 (lerobot과 같게).
4. **끝내기**
   - 보정을 모터에 쓴다 (`write_calibration`). 파일 `<이름>.json`으로 저장한다. 이미 있으면 `<이름>.json.bak-<날짜시각>`로 남긴다.
   - 팔로워는 `arm_profile.yaml`의 `robot_id`를 그 이름으로 바꾸고 접은 자세를 지운 뒤 (가운데가 바뀌어 예전 tick은 다른 자세다) 로봇 기능을 다시 켠다. 화면은 접은 자세를 다시 저장하라고 알린다. 팔로워 새로 보정은 새 이름만 받는다.
5. 어느 단계든 **취소**하면 아무것도 쓰지 않는다. 다만 2단계에서 이미 쓴 Homing_Offset은 되돌리고, 그 뒤 버스를 닫는다. 오류 · 예외 · 게이트웨이 죽음 · SIGTERM 도 되돌린다 (SIGINT 는 무시). 조작 권한이 끝나거나 비상 정지면 게이트웨이가 취소한다.
6. **되돌리기 파일**: 팔로워 보정 프로그램은 쓰기 전에 모터의 예전 보정을 `~/lekiwi_ws/arm_calibration_restore.json`에 남기고 (fsync) 되돌리거나 끝내면 지운다. 전원이 나가거나 답이 없어 죽였으면 남는다 (`status.calibration.follower.restore_pending`): '되돌리기' (`restore`) 또는 다음 새로 보정이 먼저 그 값을 모터에 쓴다.

보정 코드는 lerobot 0.6.0의 것을 쓴다 (`FeetechMotorsBus.set_half_turn_homings` · `write_calibration` · `MotorCalibration`, 장치 클래스 `LeKiwi` · `SO101Leader`의 calibrate 순서). `input()`이 있는 calibrate()를 그대로 부르지 않고, 같은 단계를 화면 버튼으로 나눈다.

## 게이트웨이 REST (조작 권한만)

| 주소 | 하는 일 |
|------|------|
| `POST /api/calibration/follower/select {robot_id}` | 팔 토크가 꺼져 있을 때만. `~/lekiwi_ws/arm_profile.yaml`(게이트웨이가 통째로 쓰는 파일, bringup이 robot_profile 뒤에 읽는다)의 `robot_id`를 바꾸고 로봇 기능을 다시 켠다. 사람이 쓴 `robot_profile.yaml`은 건드리지 않는다 |
| `POST /api/calibration/follower/from_motors {robot_id}` | `arm_registers`로 새 파일을 만든다 (이미 있으면 거절). 바퀴는 lerobot 기본값 |
| `POST /api/calibration/follower/rest_pose` | 팔 토크가 꺼져 있을 때 지금 팔 tick을 `rest_pose_ticks`로 저장 (팔로워는 로봇 기능을 다시 켤 때 읽는다) |
| `POST /api/calibration/follower/start {robot_id}` · `/middle` · `/finish` · `/cancel` | 새로 보정 (새 이름만). 단계마다 10초 안에 답이 없으면 보정 프로그램을 죽인다 |
| `POST /api/calibration/follower/restore` | 되돌리기 파일의 예전 보정 값을 모터에 쓴다 (`restore_pending`일 때) |
| `POST /api/calibration/leader/check` | 에이전트에 `calib_check` |
| `POST /api/calibration/leader/from_motors {calibration_id}` · `/start {calibration_id}` · `/middle` · `/finish` · `/cancel` | 에이전트에 넘긴다. 팔 원격 조작 세션 중에는 `check` · `from_motors` · `start`를 거절한다 |

이름(`robot_id` · `calibration_id`)은 `[A-Za-z0-9_-]{1,40}`만 받는다.

## 상태 `status.calibration`

```
follower: {
  robot_id, is_match, reason,
  files: [{id, is_match, modified}],
  motors: {ids, homing_offset, min, max},
  rest_pose_ticks,          (없으면 null)
  restore_pending,          (되돌릴 예전 보정 값이 남아 있다)
  session: {state: idle|starting|waiting_middle|recording|saving|done|error|cancelled,
            robot_id, joints: {name: {now, min, max}}, reason}
}
leader: {
  files: [{id, is_match}] (check 뒤),
  session: {...같음}
}   ← status.agent에서 온다
```

## 에이전트 규약 v2 (docs/agent_protocol.md에 더한다)

- 게이트웨이 → 에이전트:
  - `calib_check`
  - `calib_from_motors {calibration_id}`
  - `calib_start {calibration_id}`
  - `calib_middle`
  - `calib_finish`
  - `calib_cancel`
- 에이전트 → 게이트웨이:
  - `calib_status {state, calibration_id, matches: {id: bool} | null, joints: {name: {now, min, max}} | null, reason}`: 상태가 바뀔 때, recording 중에는 10 Hz.
  - `calibration_ids {calibration_ids}`: 파일 목록이 바뀌었을 때.
- 보정 중에는 리더 값을 보내지 않는다. 리더가 연결돼 있으면 먼저 끊는다.
