# coding: utf-8

import re
import socket
from fabkit import api, env, sudo, Service, filer
from fablib.base import SimpleBase

RE_CENTOS = re.compile('CentOS.*')


class Haproxy(SimpleBase):
    def __init__(self):
        self.data_key = 'haproxy'
        self.data = {}

        self.services = {
            'CentOS Linux 7.*': [
                'pcsd',
            ],
            'Ubuntu 16.*': [
                'pcsd',
            ],
        }

        self.packages = {
            'CentOS Linux 7.*': [
                'haproxy',
                'pacemaker',
                'pcs',
                'corosync',
            ],
            'Ubuntu 16.*': [
                'haproxy',
                'pacemaker',
                'pcs',
                'corosync',
                'haveged',
            ],
        }

    def init_after(self):
        for cluster in self.data.get('cluster_map', {}).values():
            if env.host in cluster['hosts']:
                self.data.update(cluster)
                break

    def setup(self):
        data = self.init()
        if RE_CENTOS.match(env.node['os']):
            sudo('setenforce 0')

        self.install_packages()

        sudo("sh -c \"echo 'hacluster:{0}' |chpasswd\"".format(data['ha_password']))
        Service('pcsd').start().enable()

    def setup_pcs(self):
        data = self.init()
        if env.host == data['hosts'][0]:
            if not filer.exists('/etc/corosync/authkey'):
                sudo('corosync-keygen')

            sudo('cp /etc/corosync/authkey /tmp/authkey')
            sudo('chmod 666 /tmp/authkey')
            api.get('/tmp/authkey', '/tmp/authkey')
            sudo('rm /tmp/authkey')

        else:
            if not filer.exists('/etc/corosync/authkey'):
                api.put('/tmp/authkey', '/tmp/authkey')
                sudo('mv /tmp/authkey /etc/corosync/authkey')
                sudo('chown root:root /etc/corosync/authkey')
                sudo('chmod 400 /etc/corosync/authkey')

        data['bindnetaddr'] = env['node']['ip']['default_dev']['subnet'].split('/')[0]
        nodes = []
        for i, host in enumerate(env.hosts):
            ip = socket.gethostbyname(host)
            nodes.append({'id': i, 'ip': ip})
        data['nodes'] = nodes
        filer.template('/etc/corosync/corosync.conf', data=data)

        with api.warn_only():
            result = sudo("pcs cluster status")
            if result.return_code != 0:
                sudo("pcs cluster start")

    def setup_pacemaker(self):
        data = self.init()

        if env.host == data['hosts'][0]:
            # stonith を無効化しておかないとresouceが作成できない?
            # sudo('pcs property set stonith-enabled=false')

            sudo('pcs resource show vip || '
                 'pcs resource create vip ocf:heartbeat:IPaddr2 '
                 'ip="{0[vip]}" cidr_netmask="{0[cidr_netmask]}" '
                 'op monitor interval="{0[monitor_interval]}s"'.format(data))

            sudo('pcs resource show lb-haproxy || '
                 'pcs resource create lb-haproxy systemd:haproxy --clone --force')
            sudo('pcs constraint order | grep "start vip then start lb-haproxy-clone" || '
                 'pcs constraint order start vip then lb-haproxy-clone')
            sudo('pcs constraint colocation | grep "vip with lb-haproxy-clone" || '
                 'pcs constraint colocation add vip with lb-haproxy-clone')

        is_change = filer.template('/etc/haproxy/haproxy.cfg', data=data)
        if env.host == data['hosts'][0] and is_change:
            Service('haproxy').reload()

        self.enable_services()
