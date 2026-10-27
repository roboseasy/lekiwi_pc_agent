"""ws_client.py: 게이트웨이 서버 코드 (websocket.py 의 사본 gateway_websocket.py) 와 프레임을 주고받는다."""

import socket
import threading
from collections.abc import Iterator

import pytest

from lekiwi_pc_agent.ws_client import WebSocketClient, WebSocketError
import gateway_websocket as websocket
from gateway_websocket import WebSocketConnection

Pair = tuple[WebSocketClient, WebSocketConnection]


@pytest.fixture
def pair() -> Iterator[Pair]:
	"""(클라이언트, 서버 쪽 WebSocketConnection) — 업그레이드 없이 소켓 쌍으로."""
	client_sock, server_sock = socket.socketpair()
	client = WebSocketClient(client_sock)
	server = WebSocketConnection(server_sock, server_sock.makefile('rb'))
	yield client, server
	client.close()
	server_sock.close()


def test_text_both_ways_with_7_16_64_bit_lengths(pair: Pair) -> None:
	client, server = pair
	for size in (5, 300, 70000):
		text = '가' * size
		client.send_text(text)
		assert server.receive() == text
		server.send_text(text)
		assert client.receive(2.0) == text


def test_receive_times_out_with_none(pair: Pair) -> None:
	client, _ = pair
	assert client.receive(0.05) is None


def test_server_ping_gets_pong_and_client_ping_gets_pong(pair: Pair) -> None:
	client, server = pair
	server._send_frame(websocket.OPCODE_PING, b'hi')
	assert client.receive(0.2) is None
	assert server._read_frame(1000)[1:] == (websocket.OPCODE_PONG, b'hi')
	before = client.last_receive_time
	client.ping()
	reader = threading.Thread(target=server.receive, daemon=True)  # receive 가 pong 으로 답한다
	reader.start()
	assert client.receive(0.5) is None
	assert client.last_receive_time > before


def test_server_close_raises_connection_error(pair: Pair) -> None:
	client, server = pair
	server.close(websocket.CLOSE_GOING_AWAY)
	with pytest.raises(ConnectionError, match='1001'):
		client.receive(1.0)
	with pytest.raises(ConnectionError):
		client.send_text('닫힌 뒤')


def test_fragmented_server_message_is_joined(pair: Pair) -> None:
	client, server = pair
	server._sock.sendall(b'\x01\x03abc' + b'\x80\x03def')  # TEXT FIN=0, CONTINUATION FIN=1
	assert client.receive(1.0) == 'abcdef'


def test_masked_server_frame_is_protocol_error(pair: Pair) -> None:
	client, server = pair
	server._sock.sendall(b'\x81\x81abcd!')
	with pytest.raises(WebSocketError, match='마스크'):
		client.receive(1.0)


def test_upgrade_refused_is_websocket_error() -> None:
	server = socket.create_server(('127.0.0.1', 0))

	def refuse() -> None:
		sock, _ = server.accept()
		sock.recv(4096)
		sock.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n')
		sock.close()

	threading.Thread(target=refuse, daemon=True).start()
	try:
		with pytest.raises(WebSocketError, match='404'):
			WebSocketClient.connect('127.0.0.1', server.getsockname()[1], '/agent', 2.0)
	finally:
		server.close()
