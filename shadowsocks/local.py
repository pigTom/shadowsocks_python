#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2012-2015 clowwindy
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

from __future__ import absolute_import, division, print_function, \
    with_statement

import sys
import os
import logging
import signal

if __name__ == '__main__':
    import inspect
    # get current dir
    file_path = os.path.dirname(os.path.realpath(inspect.getfile(inspect.currentframe())))
    sys.path.insert(0, os.path.join(file_path, '../'))

from shadowsocks import shell, daemon, eventloop, tcprelay, udprelay, asyncdns


def main():
    # Python 2.6+ or Python3.3+
    shell.check_python()

    # fix py2exe
    if hasattr(sys, "frozen") and sys.frozen in \
            ("windows_exe", "console_exe"):
        p = os.path.dirname(os.path.abspath(sys.executable))
        os.chdir(p)

    config = shell.get_config(True)

    if not config.get('dns_ipv6', False):
        asyncdns.IPV6_CONNECTION_SUPPORT = False

    # only
    daemon.daemon_exec(config)
    logging.info("local start with protocol[%s] password [%s] method [%s] obfs [%s] obfs_param [%s]" %
            (config['protocol'], config['password'], config['method'], config['obfs'], config['obfs_param']))

    try:
        logging.info("starting local at %s:%d" %
                     (config['local_address'], config['local_port']))
        # DNS分解器
        dns_resolver = asyncdns.DNSResolver()
        # TCP服务
        tcp_server = tcprelay.TCPRelay(config, dns_resolver, True)
        # UDP服务
        udp_server = udprelay.UDPRelay(config, dns_resolver, True)

        # 将DNS服务、TCP服务、UDP服务加入事件循环
        loop = eventloop.EventLoop()
        dns_resolver.add_to_loop(loop)
        tcp_server.add_to_loop(loop)
        udp_server.add_to_loop(loop)

        def handler(signum, _):
            logging.warn('received SIGQUIT, doing graceful shutting down..')
            tcp_server.close(next_tick=True)
            udp_server.close(next_tick=True)

        # 预设信号处理函数，接收到正常的退出信号
        signal.signal(getattr(signal, 'SIGQUIT', signal.SIGTERM), handler)

        def int_handler(signum, _):
            sys.exit(1)

        # SIGINT是键盘ctrl + c
        signal.signal(signal.SIGINT, int_handler)

        daemon.set_user(config.get('user', None))
        loop.run()
    except Exception as e:
        shell.print_exception(e)
        sys.exit(1)


if __name__ == '__main__':
    main()
