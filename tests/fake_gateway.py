"""시험용 가짜: 127.0.0.1 의 게이트웨이 (/agent) 와 리더암.

서버 프레임 · 핸드셰이크 검사는 게이트웨이 websocket.py 의 사본 (gateway_websocket.py) 을 쓴다.
그래서 에이전트의 클라이언트 프레임이 진짜 게이트웨이 서버 코드와 맞는지도 같이 본다. 그 모듈은 표준 라이브러리만 쓴다.
"""

import http.client
import json
import socket
import threading
import time
from collections.abc import Callable
from typing import Final

import gateway_websocket as websocket
from gateway_websocket import WebSocketConnection

MOTORS: Final[tuple[str, ...]] = (
	'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
JOINT_KEYS: Final[set[str]] = {f'arm_{motor}.pos' for motor in MOTORS}


class FakeLeader:
	"""가짜 리더암. fail_after 번 읽은 뒤부터 lerobot 처럼 읽기 오류를 낸다."""

	def __init__(self, fail_after: int | None = None) -> None:
		self.fail_after = fail_after
		self.read_count = 0
		self.is_closed = False

	def read(self) -> dict[str, float]:
		self.read_count += 1
		if self.fail_after is not None and self.read_count > self.fail_after:
			raise ConnectionError("Failed to sync read 'Present_Position'. There is no status packet!")
		return {key: float(self.read_count) for key in JOINT_KEYS}

	def close(self) -> None:
		self.is_closed = True


class FakeGateway:
	"""/agent 만 받는 가짜 게이트웨이. agent_hello 에 agent_welcome 으로 답한다.

	Attributes:
		received: 모든 연결에서 받은 (time.monotonic(), 메시지).
		connection_count: 업그레이드한 연결 수.
	"""

	def __init__(self, token: str = 'token-1', is_approved: bool = False) -> None:
		self.token = token
		self.is_approved = is_approved
		self.received: list[tuple[float, dict]] = []
		self.connection_count = 0
		self.closed_count = 0
		self._connection: WebSocketConnection | None = None
		self._condition = threading.Condition()
		self._server = socket.create_server(('127.0.0.1', 0))
		self.port = self._server.getsockname()[1]
		threading.Thread(target=self._accept_loop, daemon=True).start()

	def messages(self, kind: str) -> list[dict]:
		with self._condition:
			return [message for _, message in self.received if message['type'] == kind]

	def times(self, kind: str) -> list[float]:
		with self._condition:
			return [stamp for stamp, message in self.received if message['type'] == kind]

	def wait_for(self, predicate: Callable[[], object], timeout: float = 5.0) -> None:
		"""predicate() 가 참이 될 때까지 기다린다. 안 되면 AssertionError."""
		deadline = time.monotonic() + timeout
		with self._condition:
			while not predicate():
				remaining = deadline - time.monotonic()
				assert remaining > 0, '기다리던 메시지가 오지 않았습니다'
				self._condition.wait(min(remaining, 0.05))

	def send(self, message: dict) -> None:
		assert self._connection is not None
		self._connection.send_text(json.dumps(message))

	def drop(self) -> None:
		"""지금 연결을 끊는다 (와이파이가 끊긴 것처럼)."""
		assert self._connection is not None
		self._connection.close(websocket.CLOSE_GOING_AWAY)

	def stop(self) -> None:
		self._server.close()
		if self._connection is not None:
			self._connection.close()

	def _accept_loop(self) -> None:
		while True:
			try:
				sock, _ = self._server.accept()
			except OSError:
				return
			threading.Thread(target=self._serve, args=(sock,), daemon=True).start()

	def _serve(self, sock: socket.socket) -> None:
		reader = sock.makefile('rb')
		path = reader.readline().decode('latin-1').split(' ')[1]
		headers = http.client.parse_headers(reader)
		if path != '/agent' or websocket.handshake_error(headers) is not None:
			sock.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
			sock.close()
			return
		key = websocket.accept_key(headers['Sec-WebSocket-Key'])
		sock.sendall((
			'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
			f'Sec-WebSocket-Accept: {key}\r\n\r\n').encode('ascii'))
		connection = WebSocketConnection(sock, reader)
		with self._condition:
			self._connection = connection
			self.connection_count += 1
		while (text := connection.receive()) is not None:
			message = json.loads(text)
			with self._condition:
				self.received.append((time.monotonic(), message))
				self._condition.notify_all()
			if message['type'] == 'agent_hello':
				connection.send_text(json.dumps({
					'type': 'agent_welcome', 'token': message.get('token') or self.token,
					'is_approved': self.is_approved}))
		with self._condition:
			self.closed_count += 1
			self._condition.notify_all()
		sock.close()
