import os
import socket
import string
from random import SystemRandom

import jinja2

RNG = SystemRandom()
default_svp = {'name': '/dev/xvdf', 'size': 200,
               'type': 'standard', 'iops': None}


class AutobuilderWorker(object):
    def __init__(self, name, password, conftext=None, max_builds=1):
        self.name = name
        self.password = password
        if conftext:
            self.context = [conftext] if isinstance(conftext, str) else conftext
        else:
            self.conftext = []
        self.max_builds = max_builds
        if max_builds > 1:
            self.conftext += ['BB_NUMBER_THREADS = "${@oe.utils.cpu_count() // %d}"' % max_builds,
                              'PARALLEL_MAKE = "-j ${@oe.utils.cpu_count() // %d}"' % max_builds]


class EC2Params(object):
    def __init__(self, instance_type, ami, secgroup_ids, keypair=None,
                 region=None, subnet=None, elastic_ip=None, tags=None,
                 scratchvol=False, scratchvol_params=None,
                 instance_profile_name=None, spot_instance=False,
                 max_spot_price=None, price_multiplier=None,
                 instance_types=None, build_wait_timeout=None):
        self.instance_type = instance_type
        self.instance_types = instance_types
        self.ami = ami
        self.keypair = keypair
        self.region = region
        self.secgroup_ids = secgroup_ids
        self.subnet = subnet
        self.elastic_ip = elastic_ip
        self.tags = tags
        if build_wait_timeout:
            self.build_wait_timeout = build_wait_timeout
        else:
            self.build_wait_timeout = 0 if spot_instance else 300
        if scratchvol:
            self.scratchvolparams = scratchvol_params or default_svp
        else:
            self.scratchvolparams = None
        self.instance_profile_name = instance_profile_name
        self.spot_instance = spot_instance
        if self.spot_instance:
            if max_spot_price is None and price_multiplier is None:
                raise ValueError('You must provide either max_spot_price, or '
                                 'price_multiplier, or both, to use spot instances')
            if instance_type:
                if instance_types:
                    raise ValueError('Specify only one of instance_type, instance_types '
                                     'for spot instances')
                self.instance_types = [instance_type]
                self.instance_type = None
            else:
                if not instance_types:
                    raise ValueError('Missing instance_types for spot instance worker config')
        else:
            if instance_types:
                raise ValueError('instance_types only valid for spot instance worker configs')
            if not instance_type:
                raise ValueError('Invalid instance_type')

        self.max_spot_price = max_spot_price
        self.price_multiplier = price_multiplier


class AutobuilderEC2Worker(AutobuilderWorker):
    master_hostname = socket.gethostname()
    master_ip_address = os.getenv('MASTER_IP_ADDRESS') or socket.gethostbyname(master_hostname)
    master_fqdn = socket.getaddrinfo(master_hostname, 0, flags=socket.AI_CANONNAME)[0][3]

    def __init__(self, name, password, ec2params, conftext=None, max_builds=1,
                 userdata_template_dir=None, userdata_template_file='cloud-init.txt',
                 userdata_dict=None):
        if not password:
            password = ''.join(RNG.choice(string.ascii_letters + string.digits) for _ in range(16))
        AutobuilderWorker.__init__(self, name, password, conftext, max_builds)
        self.ec2params = ec2params
        self.ec2tags = ec2params.tags
        if self.ec2tags:
            if 'Name' not in self.ec2tags:
                tagscopy = self.ec2tags.copy()
                tagscopy['Name'] = self.name
                self.ec2tags = tagscopy
        else:
            self.ec2tags = {'Name': self.name}
        self.ec2_dev_mapping = None
        svp = ec2params.scratchvolparams
        if svp:
            ebs = {
                'VolumeType': svp['type'],
                'VolumeSize': svp['size'],
                'DeleteOnTermination': True
            }
            if 'encrypted' in svp:
                ebs['Encrypted'] = svp['encrypted']
            if svp['type'] == 'io1':
                if svp['iops']:
                    ebs['Iops'] = svp['iops']
                else:
                    ebs['Iops'] = 1000
            self.ec2_dev_mapping = [
                {'DeviceName': svp['name'], 'Ebs': ebs}
            ]
        if userdata_template_file:
            if userdata_template_dir is None:
                userdata_template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
            loader = jinja2.FileSystemLoader(userdata_template_dir)
            env = jinja2.Environment(loader=loader, undefined=jinja2.StrictUndefined)
            self.userdata_template = env.get_template(userdata_template_file)
        else:
            self.userdata_template = None
        self.userdata_extra_context = userdata_dict

    def userdata(self):
        ctx = {'workername': self.name,
               'workersecret': self.password,
               'master_ip': self.master_ip_address,
               'master_hostname': self.master_hostname,
               'master_fqdn': self.master_fqdn,
               'extra_packages': [],
               'extra_cmds': []}
        if self.userdata_extra_context:
            ctx.update(self.userdata_extra_context)
        if self.userdata_template:
            return self.userdata_template.render(ctx)
        return 'WORKERNAME="{}"\nWORKERSECRET="{}"\nMASTER="{}"\n'.format(self.name,
                                                                          self.password,
                                                                          self.master_ip_address)


