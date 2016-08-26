"""
Autobuilder slave configuration classes.
"""


class AutobuilderSlave(object):
    def __init__(self, name, password, conftext=None):
        self.name = name
        self.password = password
        self.conftext = conftext


class EC2Params(object):
    def __init__(self, instance_type, ami, keypair, secgroup,
                 region=None, subnet=None, elastic_ip=None, tags=None):
        self.instance_type = instance_type
        self.ami = ami
        self.keypair = keypair
        self.region = region
        self.secgroup = secgroup
        self.subnet = subnet
        self.elastic_ip = elastic_ip
        self.tags = tags


class AutobuilderEC2Slave(AutobuilderSlave):
    def __init__(self, name, password, ec2params, conftext=None):
        AutobuilderSlave.__init__(self, name, password, conftext)
        self.ec2params = ec2params
        self.ec2tags = ec2params.tags
        if self.ec2tags:
            if 'Name' not in self.ec2tags:
                self.ec2tags['Name'] = self.name
