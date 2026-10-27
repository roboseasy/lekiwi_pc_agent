# PC 에이전트 ↔ 게이트웨이 통신 규약 (v1, 2026-10-22)

PC(노트북)의 lerobot 리더암 프로그램(PC 에이전트)과 로봇 게이트웨이 사이의 WebSocket 메시지다.
PC와 로봇은 ROS2로 말하지 않는다. ROS2는 로봇 안에서만 돈다.

## 연결

- 주소는 `ws://<로봇>:8080/agent`이다. 브라우저용 `/ws`와 같은 서버를 쓰지만 경로가 따로다.
- 메시지는 텍스트 프레임 JSON이다. 모든 메시지에 `type`이 있다.
- 에이전트는 한 번에 하나만 연결된다. 둘째 연결은 `agent_rejected`(`reason`)를 받고 닫힌다.
- 생존 신호: 에이전트는 0.5초마다 `heartbeat`를 보낸다. 게이트웨이는 2초 동안 아무것도 받지 못하면 연결을 닫는다.

## 허락

조작 권한을 가진 브라우저가 화면에서 '이 PC 쓰기'를 눌러야 리더 값을 팔로 보낸다.

- 허락은 그 연결이 살아 있는 동안 유지된다.
- 끊긴 뒤 60초 안에 같은 `token`으로 다시 붙으면 허락이 이어진다.
- 조작 권한이 끝나면 허락도 끝난다. 다음 조작자가 다시 허락한다.

## 에이전트 → 게이트웨이

| type | 필드 | 뜻 |
|------|------|------|
| `agent_hello` | `version` (1), `host` (호스트 이름), `token` (다시 붙을 때만, 처음엔 없음), `ports` ([{`path`, `by_id`}]), `calibration_ids` ([문자열]), `lerobot_version` | 연결 직후 한 번 |
| `ports` | `ports` | 포트 목록이 바뀌었을 때 (2초마다 확인) |
| `leader_status` | `state` (`disconnected` · `connecting` · `connected` · `error`), `port`, `calibration_id`, `reason` (한국어, 오류일 때) | 리더암 상태가 바뀔 때 |
| `leader` | `seq` (정수, 1씩 증가), `joints` ({`arm_shoulder_pan.pos` … `arm_gripper.pos`: 숫자}) | 리더 관절 6개, 30 Hz, 리더가 연결돼 있을 때만. 단위는 lerobot `SO101Leader` 그대로 (도, 그리퍼 0~100) |
| `heartbeat` | — | 0.5초마다 |

## 게이트웨이 → 에이전트

| type | 필드 | 뜻 |
|------|------|------|
| `agent_welcome` | `token`, `is_approved` | `agent_hello`의 답 |
| `agent_rejected` | `reason` | 연결을 받지 않는다 (이미 다른 에이전트가 있다 등) |
| `agent_approved` / `agent_unapproved` | `reason` (unapproved만) | 허락 상태가 바뀌었다 |
| `leader_connect` | `port`, `calibration_id` | 화면에서 '리더암 연결'을 눌렀다 |
| `leader_disconnect` | — | 화면에서 '리더암 끊기'를 눌렀다 |

## 게이트웨이가 하는 일

- `leader` 값은 허락된 에이전트에게서만 받는다. 리더 값을 보내는 것은 `relay_leader`가 정하고, 팔 세션(조작 권한 + '팔 시작')이 열려 있을 때만 보낸다. 키에서 `.pos`를 떼어 `lekiwi/arm/goal`(JointState)로 낸다.
- REST (조작 권한만):
  - `POST /api/agent/approve`
  - `POST /api/agent/unapprove`
  - `POST /api/agent/leader_connect {port, calibration_id}`
  - `POST /api/agent/leader_disconnect`
- 상태 `status.agent`: `is_connected`, `host`, `address`, `is_approved`, `ports`, `calibration_ids`, `leader` ({`state`, `port`, `calibration_id`, `reason`, `rate_hz`, `age`}).

## v2: 리더 보정 (2026-10-22, docs/calibration_plan.md)

캘리브레이션 탭이 노트북 리더암의 보정을 확인 · 만들 때 쓴다. v1 메시지는 그대로다.

- 에이전트는 `agent_hello`에 `version: 2`를 보낸다. 게이트웨이는 1과 2를 둘 다 받는다 (1이면 보정을 모른다).
- 보정 이름(`calibration_id`)은 `[A-Za-z0-9_-]{1,40}`만 받는다. 파일은 노트북의 `…/calibration/teleoperators/so_leader/<이름>.json`이다.
- `calibration_ids` · `matches`에는 이 모양의 이름만 정렬해 32개까지 넣는다. `leader.v2.json` 같은 파일은 빠진다. `agent_hello`도 같다.
- 에이전트는 연 리더 포트에 `TIOCEXCL`을 건다. 그 뒤 같은 포트를 여는 프로그램은 EBUSY 로 실패한다 (root 제외).
- 버스를 쓰는 명령(`calib_check` · `calib_from_motors` · `calib_start`)은 먼저 리더를 끊는다. 스트리밍 중이면 `leader_status` `disconnected`가 먼저 온다.
- 보정 중에는 리더 값을 보내지 않는다. `leader_connect`도 `leader_status` `error`("보정 중에는…")로 거절한다.
- `port`(선택)를 주지 않으면 에이전트가 포트를 고른다.
  - 마지막 리더 포트를 먼저 쓰고, 없으면 하나뿐인 `/dev/ttyACM*`를 쓴다.
  - 둘 다 없으면 `error`다.
- 단계는 lerobot 0.6.0 `SO101Leader.calibrate()`와 같다. 코드는 `lekiwi_pc_agent/calibration.py`이고, 게이트웨이의 팔로워 보정은 이 파일의 사본(게이트웨이 저장소의 `lekiwi_web_operator/lerobot_calibration.py`)을 쓴다. 한쪽을 바꾸면 다른 쪽도 같이 바꾼다.
  - 토크 끔 → 제로-포지션 위치 시키기(`set_half_turn_homings`) → 범위 재기(wrist_roll은 0~4095) → `write_calibration` → 파일 저장.
  - 파일이 이미 있으면 `<이름>.json.bak-<YYYYmmdd-HHMMSS>`(같은 초에 또 저장하면 `-1` · `-2` …)로 복사해 둔다.
  - 저장은 임시 파일 fsync → 백업 복사 fsync → `os.replace` → 폴더 fsync 순서다. 전원이 나가도 `<이름>.json`은 예전 것 아니면 새 것이다.
  - 바퀴가 아닌 모터 가운데 토크가 켜진 것이 있으면 `calib_start`는 아무것도 쓰지 않고 `error`다 ("팔 토크가 켜져 있습니다 …").

### 게이트웨이 → 에이전트

| type | 필드 | 뜻 |
|------|------|------|
| `calib_check` | `port` (선택) | 보정 파일마다 모터와 맞는지 본다 (읽기만) |
| `calib_from_motors` | `calibration_id`, `port` (선택) | 모터 EEPROM 값으로 새 파일을 만든다. 이미 있는 이름은 거절한다 |
| `calib_start` | `calibration_id`, `port` (선택) | 새로 보정을 시작한다 (버스를 열고 토크를 끈다) |
| `calib_middle` | — | '제로-포지션 위치 시키기': Homing_Offset 을 쓴다 |
| `calib_finish` | — | '끝내기': 모터에 쓰고 파일로 저장한다 |
| `calib_cancel` | — | 취소. 제로-포지션 위치 시키기로 쓴 값(Homing_Offset · 범위)을 되돌리고 버스를 닫는다 |

### 에이전트 → 게이트웨이

| type | 필드 | 뜻 |
|------|------|------|
| `calib_status` | `state`, `calibration_id`, `matches` ({id: bool} 또는 null), `joints` ({모터: {`now`, `min`, `max`}} 또는 null), `reason` (한국어 또는 null) | 상태가 바뀔 때, `recording` 동안 10 Hz, `agent_welcome` 뒤 한 번 |
| `calibration_ids` | `calibration_ids` | 보정 파일 목록이 바뀌었을 때 (만든 직후 · 2초마다 확인) |

`state`:

| state | 뜻 |
|------|------|
| `idle` | 보정 없음. `calib_check`의 답도 `idle`이고 `matches`가 채워진다 |
| `starting` | 리더를 끊고 버스를 여는 중. 리더 스레드가 5초 안에 포트를 놓지 않으면 버스를 열지 않고 `error`다 |
| `waiting_middle` | '제로-포지션 위치 시키기'를 기다린다 |
| `recording` | 범위를 재는 중. `joints`는 tick (모터 이름은 `shoulder_pan` … `gripper`, `arm_` 없음) |
| `saving` | 모터 · 파일에 쓰는 중 |
| `done` | 저장했다 (`calib_finish` · `calib_from_motors`). `matches`를 다시 대조해 채운다. 저장 뒤 대조만 실패하면 `matches`는 null 이고 그래도 `done`이다 |
| `cancelled` | 취소했다 |
| `error` | 실패. `reason`에 이유가 있다. 하던 보정은 되돌리고 버스를 닫았다 |

- 보정 중에 할 수 없는 명령이 오면 `state`는 그대로 두고 `reason`만 채운다. 예: 움직이지 않은 관절이 있는데 `calib_finish`, 보정 중에 `calib_check`.
- 보정 중이 아닐 때 거절하면 `error`다. 예: 이미 있는 이름, 틀린 이름.
- WebSocket 이 끊겨도 보정은 이어진다. 다시 붙으면 `agent_welcome` 뒤 지금 `calib_status`를 보낸다.
- 에이전트가 끝날 때(Ctrl+C) 보정 중이면 취소해 되돌린다.

## 나중 (데이터셋 수집)

녹화할 때는 게이트웨이 → 에이전트로 관측(`observation`: 카메라 JPEG 2개 · `lekiwi/lerobot/state` · 바퀴 목표, 30 Hz)을 더한다. v1에는 없다.
