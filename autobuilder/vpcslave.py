import re
import os
import time
import boto
import boto.ec2
import boto.vpc
from boto.ec2.networkinterface import NetworkInterfaceSpecification
from boto.ec2.networkinterface import NetworkInterfaceCollection
from buildbot import interfaces
from buildbot.buildslave.ec2 import EC2LatentBuildSlave, SHUTTINGDOWN, TERMINATED
from buildbot.buildslave.base import AbstractLatentBuildSlave
from twisted.python import log


class VPCLatentBuildSlave(EC2LatentBuildSlave):

    def __init__(self, name, password, instance_type, ami=None,
                 valid_ami_owners=None, valid_ami_location_regex=None,
                 elastic_ip=None, identifier=None, secret_identifier=None,
                 aws_id_file_path=None, user_data=None, region=None,
                 keypair_name='latent_buildbot_slave',
                 security_name='latent_buildbot_slave',
                 subnet_id=None,
                 max_builds=None, notify_on_missing=[], missing_timeout=60 * 20,
                 build_wait_timeout=60 * 10, properties={}, locks=None,
                 spot_instance=False, max_spot_price=1.6, volumes=[],
                 placement=None, price_multiplier=1.2, tags={}):

        AbstractLatentBuildSlave.__init__(
            self, name, password, max_builds, notify_on_missing,
            missing_timeout, build_wait_timeout, properties, locks)
        if not ((ami is not None) ^
                (valid_ami_owners is not None or
                 valid_ami_location_regex is not None)):
            raise ValueError(
                'You must provide either a specific ami, or one or both of '
                'valid_ami_location_regex and valid_ami_owners')
        self.ami = ami
        if valid_ami_owners is not None:
            if isinstance(valid_ami_owners, (int, long)):
                valid_ami_owners = (valid_ami_owners,)
            else:
                for element in valid_ami_owners:
                    if not isinstance(element, (int, long)):
                        raise ValueError(
                            'valid_ami_owners should be int or iterable '
                            'of ints', element)
        if valid_ami_location_regex is not None:
            if not isinstance(valid_ami_location_regex, basestring):
                raise ValueError(
                    'valid_ami_location_regex should be a string')
            else:
                # verify that regex will compile
                re.compile(valid_ami_location_regex)
        self.valid_ami_owners = valid_ami_owners
        self.valid_ami_location_regex = valid_ami_location_regex
        self.instance_type = instance_type
        self.keypair_name = keypair_name
        self.security_name = security_name
        self.subnet_id = subnet_id
        self.user_data = user_data
        self.spot_instance = spot_instance
        self.max_spot_price = max_spot_price
        self.volumes = volumes
        self.price_multiplier = price_multiplier
        if None not in [placement, region]:
            self.placement = '%s%s' % (region, placement)
        else:
            self.placement = None
        if identifier is None:
            assert secret_identifier is None, (
                'supply both or neither of identifier, secret_identifier')
            if aws_id_file_path is None:
                home = os.environ['HOME']
                aws_id_file_path = os.path.join(home, '.ec2', 'aws_id')
            if not os.path.exists(aws_id_file_path):
                raise ValueError(
                    "Please supply your AWS access key identifier and secret "
                    "access key identifier either when instantiating this %s "
                    "or in the %s file (on two lines).\n" %
                    (self.__class__.__name__, aws_id_file_path))
            with open(aws_id_file_path, 'r') as aws_file:
                identifier = aws_file.readline().strip()
                secret_identifier = aws_file.readline().strip()
        else:
            assert aws_id_file_path is None, \
                'if you supply the identifier and secret_identifier, ' \
                'do not specify the aws_id_file_path'
            assert secret_identifier is not None, \
                'supply both or neither of identifier, secret_identifier'

        region_found = None

        # Make the EC2 connection.
        if region is not None:
            for r in boto.ec2.regions(aws_access_key_id=identifier,
                                      aws_secret_access_key=secret_identifier):

                if r.name == region:
                    region_found = r

            if region_found is not None:
                self.conn = boto.ec2.connect_to_region(region,
                                                       aws_access_key_id=identifier,
                                                       aws_secret_access_key=secret_identifier)
                self.vpc_conn = boto.vpc.connect_to_region(region,
                                                           aws_access_key_id=identifier,
                                                           aws_secret_access_key=secret_identifier)
            else:
                raise ValueError(
                    'The specified region does not exist: {0}'.format(region))

        else:
            self.conn = boto.connect_ec2(identifier, secret_identifier)
            self.vpc_conn = boto.connect_vpc(identifier, secret_identifier)

        # Make a keypair
        #
        # We currently discard the keypair data because we don't need it.
        # If we do need it in the future, we will always recreate the keypairs
        # because there is no way to
        # programmatically retrieve the private key component, unless we
        # generate it and store it on the filesystem, which is an unnecessary
        # usage requirement.
        try:
            key_pair = self.conn.get_all_key_pairs(keypair_name)[0]
            assert key_pair
            # key_pair.delete() # would be used to recreate
        except boto.exception.EC2ResponseError, e:
            if 'InvalidKeyPair.NotFound' not in e.body:
                if 'AuthFailure' in e.body:
                    print ('POSSIBLE CAUSES OF ERROR:\n'
                           '  Did you sign up for EC2?\n'
                           '  Did you put a credit card number in your AWS '
                           'account?\n'
                           'Please doublecheck before reporting a problem.\n')
                raise
            # make one; we would always do this, and stash the result, if we
            # needed the key (for instance, to SSH to the box).  We'd then
            # use paramiko to use the key to connect.
            self.conn.create_key_pair(keypair_name)

        # create security group
        security_group_filter = {'group-name': security_name}

        if self.subnet_id:
            self.subnet = self.vpc_conn.get_all_subnets(self.subnet_id)[0]
            security_group_filter['vpc-id'] = self.subnet.vpc_id
        else:
            self.subnet = None

        # Find the security group.  DO NOT auto-create.
        self.security_group = self.conn.get_all_security_groups(filters=security_group_filter)[0]

        # get the image
        if self.ami is not None:
            self.image = self.conn.get_image(self.ami)
            self.block_device_mapping = self.image.block_device_mapping
        else:
            # verify we have access to at least one acceptable image
            discard = self.get_image()
            assert discard
            self.block_device_mapping = None

        # The API doesn't allow us to specify a value (even False) for 'encrypted'
        # if there is a snapshot ID.
        if self.block_device_mapping:
            for bdm in self.block_device_mapping:
                if self.block_device_mapping[bdm].snapshot_id is not None:
                    self.block_device_mapping[bdm].encrypted = None

        # allocate a dynamic elastic IP, if requested
        # otherwise, if an elastic IP is specified, use it
        self.dynamic_ip = False
        if elastic_ip == 'dynamic':
            assert self.subnet
            self.dynamic_ip = True
            self.elastic_ip = None
        elif elastic_ip is not None:
            self.elastic_ip = self.conn.get_all_addresses([elastic_ip])[0]
        self.tags = tags

    def _start_instance(self):
        image = self.get_image()

        # If running in a VPC, use the network interface specification to
        # pass the subnet, security group IDs, and request for public IP.
        # Otherwise, use the EC2-classic method.
        if self.subnet:
            group_names = None
            netifspec = NetworkInterfaceSpecification(subnet_id=self.subnet_id,
                                                      groups=[self.security_group.id],
                                                      associate_public_ip_address=self.dynamic_ip)
            netifcoll = NetworkInterfaceCollection(netifspec)
        else:
            group_names = [self.security_name]
            netifcoll = None

        reservation = self.conn.run_instances(image.id,
                                              key_name=self.keypair_name,
                                              security_groups=group_names,
                                              instance_type=self.instance_type,
                                              user_data=self.user_data,
                                              placement=self.placement,
                                              block_device_map=self.block_device_mapping,
                                              network_interfaces=netifcoll)
        self.instance = reservation.instances[0]
        instance_id, image_id, start_time = self._wait_for_instance(
            reservation)
        if None not in [instance_id, image_id, start_time]:
            if len(self.tags) > 0:
                self.conn.create_tags(instance_id, self.tags)
            return [instance_id, image_id, start_time]
        else:
            log.msg('%s %s failed to start instance %s (%s)' %
                    (self.__class__.__name__, self.slavename,
                     self.instance.id, self.instance.state))
            raise interfaces.LatentBuildSlaveFailedToSubstantiate(
                self.instance.id, self.instance.state)

    def _stop_instance(self, instance, fast):
        if self.elastic_ip is not None and not self.dynamic_ip:
            self.conn.disassociate_address(public_ip=self.elastic_ip.public_ip)
        instance.update()
        if instance.state not in (SHUTTINGDOWN, TERMINATED):
            instance.terminate()
            log.msg('%s %s terminating instance %s' %
                    (self.__class__.__name__, self.slavename, instance.id))
        duration = 0
        interval = self._poll_resolution
        if fast:
            goal = (SHUTTINGDOWN, TERMINATED)
            instance.update()
        else:
            goal = (TERMINATED,)
        while instance.state not in goal:
            time.sleep(interval)
            duration += interval
            if duration % 60 == 0:
                log.msg(
                    '%s %s has waited %d minutes for instance %s to end' %
                    (self.__class__.__name__, self.slavename, duration // 60,
                     instance.id))
            instance.update()
        log.msg('%s %s instance %s %s '
                'after about %d minutes %d seconds' %
                (self.__class__.__name__, self.slavename,
                 instance.id, goal, duration // 60, duration % 60))
