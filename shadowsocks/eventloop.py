#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright 2013-2015 clowwindy
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# from ssloop
# https://github.com/clowwindy/ssloop

from __future__ import absolute_import, division, print_function, \
    with_statement

import os
import time
import socket
import select
import errno
import logging
from collections import defaultdict

from shadowsocks import shell

__all__ = ['EventLoop', 'POLL_NULL', 'POLL_IN', 'POLL_OUT', 'POLL_ERR',
           'POLL_HUP', 'POLL_NVAL', 'EVENT_NAMES']

POLL_NULL = 0x00
# 0000 0001 read mode
POLL_IN = 0x01
# 0000 0100 write mode
POLL_OUT = 0x04
# 0000 1000
POLL_ERR = 0x08
# 0001 0000
POLL_HUP = 0x10
# 0010 0000
POLL_NVAL = 0x20

EVENT_NAMES = {
    POLL_NULL: 'POLL_NULL',
    POLL_IN: 'POLL_IN',
    POLL_OUT: 'POLL_OUT',
    POLL_ERR: 'POLL_ERR',
    POLL_HUP: 'POLL_HUP',
    POLL_NVAL: 'POLL_NVAL',
}

# we check timeouts every TIMEOUT_PRECISION seconds
TIMEOUT_PRECISION = 2


class KqueueLoop(object):
    """
    Used in BSD linux system
    将Kuquene封装
    """

    MAX_EVENTS = 1024

    def __init__(self):
        # Only supported on BSD
        self._kqueue = select.kqueue()
        self._fds = {}

    def _control(self, fd, mode, flags):
        events = []
        if mode & POLL_IN:
            events.append(select.kevent(fd, select.KQ_FILTER_READ, flags))
        if mode & POLL_OUT:
            events.append(select.kevent(fd, select.KQ_FILTER_WRITE, flags))
        for e in events:
            self._kqueue.control([e], 0)

    def poll(self, timeout):
        """
        列出队列中的事件数
        :param timeout:
        :return: dict_items([(fd, EVENT_NAMES),......])
        """
        if timeout < 0:
            timeout = None  # kqueue behaviour
        events = self._kqueue.control(None, KqueueLoop.MAX_EVENTS, timeout)
        results = defaultdict(lambda: POLL_NULL)
        for e in events:
            fd = e.ident
            if e.filter == select.KQ_FILTER_READ:
                results[fd] |= POLL_IN
            elif e.filter == select.KQ_FILTER_WRITE:
                results[fd] |= POLL_OUT
        return results.items()

    def register(self, fd, mode):
        """注册一个事件"""
        self._fds[fd] = mode
        # KQ_EV_ADD means Adds or modifies an event
        self._control(fd, mode, select.KQ_EV_ADD)

    def unregister(self, fd):
        """注销一个事件"""
        # KQ_EV_DELETE Removes an event from the queue
        self._control(fd, self._fds[fd], select.KQ_EV_DELETE)
        del self._fds[fd]

    def modify(self, fd, mode):
        self.unregister(fd)
        self.register(fd, mode)

    def close(self):
        self._kqueue.close()


class SelectLoop(object):

    def __init__(self):
        self._r_list = set()
        self._w_list = set()
        self._x_list = set()

    def poll(self, timeout):
        """
               列出Select中的事件数
               map of key fd, value POLL TYPE: POLL_IN, POLL_OUT, POLL_ERR
               :param timeout:
               :return: [(fd, mode)]
               """
        # x_list wait for exceptional condition
        r, w, x = select.select(self._r_list, self._w_list, self._x_list,
                                timeout)
        results = defaultdict(lambda: POLL_NULL)
        for p in [(r, POLL_IN), (w, POLL_OUT), (x, POLL_ERR)]:
            for fd in p[0]:
                results[fd] |= p[1]
        return results.items()

    def register(self, fd, mode):
        """
        注册一个事件
        将fd加入list中
        如果是读加入r_list中
        如果是写加入w_list中
        如果是其它加入x_list中
        :param fd: 文件描述符
        :param mode: @see EVENT_NAMES
        :return:
        """
        if mode & POLL_IN:
            self._r_list.add(fd)
        if mode & POLL_OUT:
            self._w_list.add(fd)
        if mode & POLL_ERR:
            self._x_list.add(fd)

    def unregister(self, fd):
        """
        注销一个事件
        :param fd:
        :return:
        """
        if fd in self._r_list:
            self._r_list.remove(fd)
        if fd in self._w_list:
            self._w_list.remove(fd)
        if fd in self._x_list:
            self._x_list.remove(fd)

    def modify(self, fd, mode):
        """
        修改一个事件
        :param fd:
        :param mode:
        :return:
        """
        self.unregister(fd)
        self.register(fd, mode)

    def close(self):
        pass


class EventLoop(object):
    def __init__(self):
        if hasattr(select, 'epoll'):
            self._impl = select.epoll()
            model = 'epoll'
        elif hasattr(select, 'kqueue'):
            self._impl = KqueueLoop()
            model = 'kqueue'
        elif hasattr(select, 'select'):
            self._impl = SelectLoop()
            model = 'select'
        else:
            raise Exception('can not find any available functions in select '
                            'package')
        self._fdmap = {}  # (f, handler)
        self._last_time = time.time()
        self._periodic_callbacks = []
        self._stopping = False
        logging.debug('using event model: %s', model)

    def poll(self, timeout=None):
        """
        返回 [(file, fd, event)
        :param timeout:
        :return:
        """
        events = self._impl.poll(timeout)
        return [(self._fdmap[fd][0], fd, event) for fd, event in events]

    def add(self, f, mode, handler):
        """
        加入文件， 模式和处理器到事件循环中
        :param f: 文件（socket)
        :param mode: 读或写等
        :param handler: 处理器
        :return:
        """
        fd = f.fileno()
        self._fdmap[fd] = (f, handler)
        self._impl.register(fd, mode)

    def remove(self, f):
        """
        通过文件注销事件循环中的注册的事件
        :param f: 文件
        :return:
        """
        fd = f.fileno()
        del self._fdmap[fd]
        self._impl.unregister(fd)

    def removefd(self, fd):
        """
        移除注销事件循环中的事件
        :param fd:
        :return:
         #remove
        """
        del self._fdmap[fd]
        self._impl.unregister(fd)

    def add_periodic(self, callback):
        """
        加入回调函数,回调函数主要当socket关闭之后，后续关闭相关操作
        :param callback: 回调函数
        :return:
        """
        self._periodic_callbacks.append(callback)

    def remove_periodic(self, callback):
        """
        移除回调函数
        :param callback:
        :return:
        """
        self._periodic_callbacks.remove(callback)

    def modify(self, f, mode):
        """
        修改事件
        :param f: 文件类型
        :param mode: 事件类型
        :return: null
        """
        fd = f.fileno()
        self._impl.modify(fd, mode)

    def stop(self):
        """停止"""
        self._stopping = True

    def run(self):
        """
        不停地取出队列中的事件，并用handler进行处理
        :return:
        """
        events = []
        while not self._stopping:
            asap = False
            try:
                # event is [(file, fd, mode)]
                events = self.poll(TIMEOUT_PRECISION)
            except (OSError, IOError) as e:
                if errno_from_exception(e) in (errno.EPIPE, errno.EINTR):
                    # EPIPE: Happens when the client closes the connection
                    # EINTR: Happens when received a signal
                    # handles them as soon as possible
                    asap = True
                    logging.debug('poll:%s', e)
                else:
                    # 未知错误
                    logging.error('poll:%s', e)
                    import traceback
                    traceback.print_exc()
                    continue

            handle = False
            # event is kind of mode
            for sock, fd, event in events:
                handler = self._fdmap.get(fd, None)
                if handler is not None:
                    handler = handler[1]
                    try:
                        #  handle event
                        handle = handler.handle_event(sock, fd, event) or handle
                    except (OSError, IOError) as e:
                        shell.print_exception(e)
            now = time.time()
            if asap or now - self._last_time >= TIMEOUT_PRECISION:
                # 回调函数，是用来关掉socket连接的
                for callback in self._periodic_callbacks:
                    callback()
                self._last_time = now
            if events and not handle:
                # sleep for 1 milli second
                time.sleep(0.001)

    def __del__(self):
        self._impl.close()


# from tornado
def errno_from_exception(e):
    """Provides the errno from an Exception object.

    There are cases that the errno attribute was not set so we pull
    the errno out of the args but if someone instatiates an Exception
    without any args you will get a tuple error. So this function
    abstracts all that behavior to give you a safe way to get the
    errno.
    """

    if hasattr(e, 'errno'):
        return e.errno
    elif e.args:
        return e.args[0]
    else:
        return None


# from tornado
def get_sock_error(sock):
    error_number = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
    return socket.error(error_number, os.strerror(error_number))
