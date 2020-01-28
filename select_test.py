import socket
import logging
import signal
import sys
from shadowsocks import eventloop, common

BUFFER_SIZE = 1024


class ServerTest:
    def __init__(self, loop, port, ip=""):
        self._ip = ip
        self._port = port
        # init a server
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((ip, port))
        self._socket.listen(1024)
        self._loop = loop
        self._socks = []
        self._socks.append(self._socket)
        self._read_data = []
        self._add_to_loop(self._socket, eventloop.POLL_IN)

    def _add_to_loop(self, sock, event):
        self._loop.add(sock, eventloop.POLL_ERR | event, self)

    def _modify(self, sock, event):
        self._loop.modify(sock, eventloop.POLL_ERR | event)

    def handle_event(self, sock, fd, event):
        # sock为_socket,则是接收到了客户端的连接
        # connect socket
        if self._socket == sock:
            conn = self._socket.accept()
            # 将conn加入到eventloop中
            self._socks.append(conn[0])
            self._add_to_loop(conn[0], eventloop.POLL_IN)

        # stream socket
        else:
            if event & eventloop.POLL_ERR:
                print("fileno is %s" % fd)
            # 有可读事件
            elif event & eventloop.POLL_IN:
                data = sock.recv(BUFFER_SIZE)
                self._read_data.append(data)
                print("server read from client: ", data.decode("utf-8"))
                self._modify(sock, eventloop.POLL_OUT)

            elif event & eventloop.POLL_OUT:
                if self._read_data:

                    for read_data in self._read_data:
                        sock.send(read_data)
                    self._read_data = []
                else:
                    sock.send(b"bye bye")
                self._modify(sock, eventloop.POLL_IN)

    def close(self, next_tick):
        for con in self._socks:
            self._loop.remove(con)


if __name__ == '__main__':
    loop_event = eventloop.EventLoop()
    server = ServerTest(loop_event, 9999)

    def handler(signum, _):
        logging.warn('received SIGQUIT, doing graceful shutting down..')
        server.close(next_tick=True)


    # 预设信号处理函数，接收到正常的退出信号
    signal.signal(getattr(signal, 'SIGQUIT', signal.SIGTERM), handler)


    def int_handler(signum, _):
        sys.exit(1)


    # SIGINT是键盘ctrl + c
    signal.signal(signal.SIGINT, int_handler)
    loop_event.run()
