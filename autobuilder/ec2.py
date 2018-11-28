import re
import os
from buildbot.plugins import worker
from buildbot.worker import AbstractLatentWorker
import boto3
import botocore
from botocore.client import ClientError


class MyEC2LatentWorker(worker.EC2LatentWorker):
    def __init__(self, name, password, instance_type, ami=None,
                 valid_ami_owners=None, valid_ami_location_regex=None,
                 elastic_ip=None, identifier=None, secret_identifier=None,
                 aws_id_file_path=None, user_data=None, region=None,
                 keypair_name=None,
                 security_name=None,
                 spot_instance=False, max_spot_price=1.6, volumes=None,
                 placement=None, price_multiplier=1.2, tags=None,
                 product_description='Linux/UNIX',
                 subnet_id=None, security_group_ids=None, instance_profile_name=None,
                 block_device_map=None, session=None,
                 **kwargs):

        if volumes is None:
            volumes = []

        if tags is None:
            tags = {}

        # noinspection PyCallByClass
        AbstractLatentWorker.__init__(self, name, password, **kwargs)

        if security_name and subnet_id:
            raise ValueError(
                'security_name (EC2 classic security groups) is not supported '
                'in a VPC.  Use security_group_ids instead.')
        if not ((ami is not None) ^
                (valid_ami_owners is not None or
                 valid_ami_location_regex is not None)):
            raise ValueError(
                'You must provide either a specific ami, or one or both of '
                'valid_ami_location_regex and valid_ami_owners')
        self.ami = ami
        if valid_ami_owners is not None:
            if isinstance(valid_ami_owners, integer_types):
                valid_ami_owners = (valid_ami_owners,)
            else:
                for element in valid_ami_owners:
                    if not isinstance(element, integer_types):
                        raise ValueError(
                            'valid_ami_owners should be int or iterable '
                            'of ints', element)
        if valid_ami_location_regex is not None:
            if not isinstance(valid_ami_location_regex, string_types):
                raise ValueError(
                    'valid_ami_location_regex should be a string')
            else:
                # verify that regex will compile
                re.compile(valid_ami_location_regex)
        if spot_instance and price_multiplier is None and max_spot_price is None:
            raise ValueError('You must provide either one, or both, of '
                             'price_multiplier or max_spot_price')
        self.valid_ami_owners = None
        if valid_ami_owners:
            self.valid_ami_owners = [str(o) for o in valid_ami_owners]
        self.valid_ami_location_regex = valid_ami_location_regex
        self.instance_type = instance_type
        self.keypair_name = keypair_name
        self.security_name = security_name
        self.user_data = user_data
        self.spot_instance = spot_instance
        self.max_spot_price = max_spot_price
        self.volumes = volumes
        self.price_multiplier = price_multiplier
        self.product_description = product_description

        if None not in [placement, region]:
            self.placement = '%s%s' % (region, placement)
        else:
            self.placement = None
        if identifier is None:
            assert secret_identifier is None, (
                'supply both or neither of identifier, secret_identifier')
            if aws_id_file_path is None:
                home = os.environ['HOME']
                default_path = os.path.join(home, '.ec2', 'aws_id')
                if os.path.exists(default_path):
                    aws_id_file_path = default_path
            if aws_id_file_path:
                log.msg('WARNING: EC2LatentWorker is using deprecated '
                        'aws_id file')
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
        self.session = session
        if self.session is None:
            if region is not None:
                for r in boto3.Session(
                        aws_access_key_id=identifier,
                        aws_secret_access_key=secret_identifier).get_available_regions('ec2'):

                    if r == region:
                        region_found = r

                if region_found is not None:
                    self.session = boto3.Session(
                        region_name=region,
                        aws_access_key_id=identifier,
                        aws_secret_access_key=secret_identifier)
                else:
                    raise ValueError(
                        'The specified region does not exist: ' + region)

            else:
                # boto2 defaulted to us-east-1 when region was unset, we
                # mimic this here in boto3
                region = botocore.session.get_session().get_config_variable('region')
                if region is None:
                    region = 'us-east-1'
                self.session = boto3.Session(
                    aws_access_key_id=identifier,
                    aws_secret_access_key=secret_identifier,
                    region_name=region
                )

        self.ec2 = self.session.resource('ec2')
        self.ec2_client = self.session.client('ec2')

        # Make a keypair
        #
        # We currently discard the keypair data because we don't need it.
        # If we do need it in the future, we will always recreate the keypairs
        # because there is no way to
        # programmatically retrieve the private key component, unless we
        # generate it and store it on the filesystem, which is an unnecessary
        # usage requirement.
        if self.keypair_name:
            try:
                self.ec2.KeyPair(self.keypair_name).load()
                # key_pair.delete() # would be used to recreate
            except ClientError as e:
                if 'InvalidKeyPair.NotFound' not in str(e):
                    if 'AuthFailure' in str(e):
                        log.msg('POSSIBLE CAUSES OF ERROR:\n'
                                '  Did you supply your AWS credentials?\n'
                                '  Did you sign up for EC2?\n'
                                '  Did you put a credit card number in your AWS '
                                'account?\n'
                                'Please doublecheck before reporting a problem.\n')
                        raise
                    # make one; we would always do this, and stash the result, if we
                    # needed the key (for instance, to SSH to the box).  We'd then
                    # use paramiko to use the key to connect.
                    self.ec2.create_key_pair(KeyName=keypair_name)

        # create security group
        if security_name:
            try:
                self.ec2_client.describe_security_groups(GroupNames=[security_name])
            except ClientError as e:
                if 'InvalidGroup.NotFound' in str(e):
                    self.security_group = self.ec2.create_security_group(
                        GroupName=security_name,
                        Description='Authorization to access the buildbot instance.')
                    # Authorize the master as necessary
                    # TODO this is where we'd open the hole to do the reverse pb
                    # connect to the buildbot
                    # ip = urllib.urlopen(
                    #     'http://checkip.amazonaws.com').read().strip()
                    # self.security_group.authorize('tcp', 22, 22, '%s/32' % ip)
                    # self.security_group.authorize('tcp', 80, 80, '%s/32' % ip)
                else:
                    raise

        # get the image
        if self.ami is not None:
            self.image = self.ec2.Image(self.ami)
        else:
            # verify we have access to at least one acceptable image
            discard = self.get_image()
            assert discard

        # get the specified elastic IP, if any
        if elastic_ip is not None:
            # Using ec2.vpc_addresses.filter(PublicIps=[elastic_ip]) throws a
            # NotImplementedError("Filtering not supported in describe_address.") in moto
            # https://github.com/spulec/moto/blob/100ec4e7c8aa3fde87ff6981e2139768816992e4/moto/ec2/responses/elastic_ip_addresses.py#L52
            addresses = self.ec2.meta.client.describe_addresses(
                PublicIps=[elastic_ip])['Addresses']
            if not addresses:
                raise ValueError(
                    'Could not find EIP for IP: ' + elastic_ip)
            allocation_id = addresses[0]['AllocationId']
            elastic_ip = self.ec2.VpcAddress(allocation_id)
        self.elastic_ip = elastic_ip
        self.subnet_id = subnet_id
        self.security_group_ids = security_group_ids
        self.classic_security_groups = [
            self.security_name] if self.security_name else None
        self.instance_profile_name = instance_profile_name
        self.tags = tags
        self.block_device_map = self.create_block_device_mapping(
            block_device_map) if block_device_map else None

    def _start_instance(self):
        image = self.get_image()
        launch_opts = dict(
            ImageId=image.id, KeyName=self.keypair_name,
            SecurityGroups=self.classic_security_groups,
            InstanceType=self.instance_type, UserData=self.user_data,
            Placement=self.placement, MinCount=1, MaxCount=1,
            NetworkInterfaces=[{'AssociatePublicIpAddress': True,
                                'DeviceIndex': 0,
                                'Groups': self.security_group_ids,
                                'SubnetId': self.subnet_id}],
            IamInstanceProfile=self._remove_none_opts(
                Name=self.instance_profile_name,
            ),
            BlockDeviceMappings=self.block_device_map
        )

        launch_opts = self._remove_none_opts(launch_opts)
        reservations = self.ec2.create_instances(**launch_opts)

        self.instance = reservations[0]
        instance_id, start_time = self._wait_for_instance()
        if None not in [instance_id, image.id, start_time]:
            return [instance_id, image.id, start_time]
        else:
            self.failed_to_start(self.instance.id, self.instance.state['Name'])
