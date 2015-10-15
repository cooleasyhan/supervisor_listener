#!/usr/bin/env python
# -*- coding: UTF-8 -*-
'''
Supervisor listener
'''
import xmlrpclib
import logging
import socket
import time
import Queue
import threading


log = logging.getLogger(__name__)
# log.addHandler(logging.NullHandler)


ALARM_QUEUE = Queue.Queue()


class SupervisorRPC(object):

    '''SupervisorRPC'''

    def __init__(self, name, rpc_url):
        log.debug('[%s]init SupervisorRPC, url: %s', name, rpc_url)
        self.server = xmlrpclib.Server(rpc_url)
        self.server_name = name
        self.server_url = rpc_url
        self.state = None
        self.all_process_info = None
        self.refresh()

    def refresh(self):
        '''refresh'''
        self.state = self.server.supervisor.getState()
        self.all_process_info = self.server.supervisor.getAllProcessInfo()
        log.debug(
            '[%s]-----------refresh rst begin------------', self.server_name)
        log.debug('[%s] %s', self.server_name, str(self.state))
        # log.debug('[%s] %s', self.server_name, self.all_process_info)
        log.debug(
            '[%s]-----------refresh rst end--------------', self.server_name)

    def stop_process(self, name):
        '''stop_process'''
        log.info('[%s]stop_process: %s', self.server_name, name)
        self.server.supervisor.stopProcess(name)
        self.refresh()

    def start_process(self, name):
        '''start_process'''
        log.info('[%s]start_process: %s', self.server_name, name)
        self.server.supervisor.startProcess(name)
        self.refresh()

    def is_alive(self):
        '''
        statecode   statename   Description
        2   FATAL   Supervisor has experienced a serious error.
        1   RUNNING Supervisor is working normally.
        0   RESTARTING  Supervisor is in the process of restarting.
        -1  SHUTDOWN    Supervisor is in the process of shutting down.
        '''
        cnt = 3
        while cnt >= 0:
            cnt -= 1
            self.refresh()
            state = self.state['statecode']
            log.info(
                '[%s]supervisor statecode: %s', self.server_name, str(state))
            if state == 1:
                return True
            if state == 2:
                return False

            if state in (0, -1):
                time.sleep(5)

        return False

    def get_process_info(self, name):
        '''
        {'name':           'process name',
         'group':          'group name',
         'start':          1200361776,
         'stop':           0,
         'now':            1200361812,
         'state':          1,
         'statename':      'RUNNING',
         'spawnerr':       '',
         'exitstatus':     0,
         'stdout_logfile': '/path/to/stdout-log',
         'stderr_logfile': '/path/to/stderr-log',
         'pid':            1}
        '''

        default = dict()
        default['state'] = 1000
        for prc in self.all_process_info:
            if prc['name'] == name:
                return prc

        return default

    def process_is_running(self, name):
        '''
        STOPPED (0)
        The process has been stopped due to a stop request or has never been started.

        STARTING (10)
        The process is starting due to a start request.

        RUNNING (20)
        The process is running.

        BACKOFF (30)
        The process entered the STARTING state but subsequently exited too quickly to move to the RUNNING state.

        STOPPING (40)
        The process is stopping due to a stop request.

        EXITED (100)
        The process exited from the RUNNING state (expectedly or unexpectedly).

        FATAL (200)
        The process could not be started successfully.

        UNKNOWN (1000)
        The process is in an unknown state (supervisord programming error).

        '''

        cnt = 3
        while cnt >= 0:
            cnt -= 1
            self.refresh()
            state = self.get_process_info(name)['state']
            log.info(
                '[%s:process:%s] statecode: %s', self.server_name,
                name, str(state))
            if state == 20:
                return True
            if state in (0, 200):
                return False

            time.sleep(5)


class SupervisorListen(object):

    '''SupervisorListen'''

    def __init__(self, rpc_config=None, interval=60):
        '''rpc_config['name']
           rpc_config['url']
        '''
        self.interval = interval
        self.server_list = dict()
        if rpc_config:
            for name, url in rpc_config.items:
                self.server_list[name] = SupervisorRPC(name, url)

    def add_supervisor(self, supervisor):
        self.server_list[supervisor.server_name] = supervisor

    def send_alarm(self, warn_msg):
        '''send_alarm'''
        ALARM_QUEUE.put(warn_msg)

    def linsten_alive(self):
        '''linsten_alive'''

        while True:
            warn_msg = ''
            for server in self.server_list.values():
                log.info(
                    '[%s]check alive %s', server.server_name, server.server_url)
                try:
                    if not server.is_alive():
                        warn_msg += '(%s)url %s is down' % (
                            server.server_name, server.server_url)
                except socket.error, socket_error:
                    log.exception(socket_error)
                    warn_msg += '(%s) url %s is donw' % (
                        server.server_name, server.server_url)
                except Exception, exc:
                    log.exception(exc)
                    warn_msg += '(%s)url %s get unknow exception ' % (
                        server.server_name, server.server_url)

            if warn_msg:
                log.warning('warn_msg: %s', warn_msg)
                self.send_alarm(warn_msg)

            time.sleep(self.interval)

    def start_linsten_alive_thread(self):
        thread = threading.Thread(target=self.linsten_alive)
        thread.daemon = True
        thread.start()
        return thread

    def linsten_master_slave_process(self, master, slave, process_list):
        '''linsten_master_slave'''
        master_server = self.server_list[master]
        slave_server = self.server_list[slave]

        while True:
            try:
                warn_msg = ''
                # check master is alive, and the process is running
                for process in process_list:
                    process_name = process.split(':')[-1:][0]

                    try:
                        master_state = master_server.process_is_running(
                            process_name)
                    except Exception, exc:
                        log.exception(exc)
                        master_state = False
                    try:
                        slave_state = slave_server.process_is_running(process_name)
                    except Exception, exc:
                        log.exception(exc)
                        slave_state = False

                    if master_state and not slave_state:
                        log.info('[%s-%s], %s is ok', master, slave, process)
                    elif not master_state and slave_state:
                        log.info(
                            '[%s-%s], %s is running in slave', master, slave, process)
                    elif not master_state and not slave_state:
                        warn_msg += '(%s-%s), %s is stoped in master and slave, \
                            try to start slave' % (master, slave, process)

                        slave_server.start_process(process)
                    else:
                        warn_msg += '(%s-%s), %s is running in master and slave, \
                            try to stop slave' % (master, slave, process)

                        slave_server.stop_process(process)
            except Exception, exc:
                log.exception(exc)
                warn_msg += '(%s-%s), %s get unkonw error' % (
                    master, slave, process)

            if warn_msg:
                log.warning('warn_msg: %s', warn_msg)
                self.send_alarm(warn_msg)

            time.sleep(self.interval)

    def start_linsten_master_slave_thread(self, master, slave, process):
        thread = threading.Thread(target=self.linsten_master_slave_process,
                                  args=(master, slave, process))
        thread.daemon = True
        thread.start()
        return thread
