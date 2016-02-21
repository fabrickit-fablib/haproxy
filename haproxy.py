# coding: utf-8

from fabkit import api, env, sudo, Service, filer
from fablib.base import SimpleBase


class Haproxy(SimpleBase):
    def __init__(self):
        self.data_key = 'haproxy'
        self.data = {}

        self.services = {
            'CentOS Linux 7.*': [
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
        }

    def init_after(self):
        for cluster in self.data.get('cluster_map', {}).values():
            if env.host in cluster['hosts']:
                self.data.update(cluster)
                break

    def setup(self):
        self.init()
        sudo('setenforce 0')
        self.install_packages()

        sudo("sh -c \"echo 'hacluster:hapass' |chpasswd\"")
        Service('pcsd').start().enable()

    def setup_pcs(self):
        data = self.init()
        if env.host == data['hosts'][0]:
            with api.warn_only():
                result = sudo("pcs cluster status")
                if result.return_code != 0:
                    sudo("pcs cluster auth 192.168.122.50 192.168.122.51 -u hacluster -p hapass")

                    # pcs cluster setup で/etc/corosync/corosync.conf が自動生成される
                    sudo("pcs cluster setup --name hacluster 192.168.122.50 192.168.122.51")
                    sudo("pcs cluster start --all")
                    sudo("corosync-cfgtool -s")

                    # 片系をpcs cluster stop でもう一方へfail over
                    # cluster start で復帰

                    # rebootでもstopされfail over
                    # reboot後にcluster start で復帰

    def setup_pacemaker(self):
        data = self.init()

        if env.host == data['hosts'][0]:
            # stonith を無効化しておかないとresouceが作成できない
            sudo('pcs property set stonith-enabled=false')
            sudo('pcs resource show vip || '
                 'pcs resource create vip ocf:heartbeat:IPaddr2 '
                 'ip="{0[vip]}" cidr_netmask="{0[cidr_netmask]}" '
                 'op monitor interval="{0[monitor_interval]}s"'.format(data))

            sudo('pcs resource show lb-haproxy || '
                 'pcs resource create lb-haproxy systemd:haproxy --clone')
            sudo('pcs constraint order | grep "start vip then start lb-haproxy-clone" || '
                 'pcs constraint order start vip then lb-haproxy-clone')
            sudo('pcs constraint colocation | grep "vip with lb-haproxy-clone" || '
                 'pcs constraint colocation add vip with lb-haproxy-clone')

        is_change = filer.template('/etc/haproxy/haproxy.cfg', data=data)
        if env.host == data['hosts'][0] and is_change:
            Service('haproxy').reload()

        self.enable_services()
