import base64
import datetime
import os
import re
import time

import boto3
import botocore
import botocore.exceptions
import botocore.session
from botocore.client import ClientError
from buildbot.interfaces import LatentWorkerFailedToSubstantiate
from buildbot.plugins import worker
from buildbot.worker import AbstractLatentWorker
from twisted.python import log


class MyEC2LatentWorker(worker.EC2LatentWorker):
    # Default quarantine timeout intervals are much too short for EC2.
    quarantine_timeout = quarantine_initial_timeout = 15 * 60
    quarantine_max_timeout = 24 * 60 * 60

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
                 instance_types=None,
                 subnet_ids=None,
                 **kwargs):

        if volumes is None:
            volumes = []

        if tags is None:
            tags = {}

        if spot_instance:
            if instance_types is None:
                if instance_type:
                    self.instance_types = [instance_type]
                else:
                    raise ValueError('one of instance_type or instance_types must be provided')
            else:
                if instance_type:
                    raise ValueError('only one of instance_type or instance_types should be provided')
                else:
                    self.instance_types = instance_types
            if subnet_ids is None:
                if subnet_id:
                    self.subnet_ids = [subnet_id]
            else:
                if subnet_id:
                    raise ValueError('only one of subnet_id or subnet_ids should be provided')
                else:
                    self.subnet_ids = subnet_ids
        else:
            if instance_types:
                raise ValueError('instance_types only valid for spot_instance workers')
            if subnet_ids is not None:
                raise ValueError('subnet_ids only valid for spot instances')

        # noinspection PyCallByClass
        AbstractLatentWorker.__init__(self, name, password, **kwargs)

        # Set the default build wait timeout lower
        #   no wait for spot instances - they're apt to time out before completing a second build anyway
        #   5 minutes for on-demand instances
        if 'build_wait_timeout' not in kwargs:
            self.build_wait_timeout = 0 if spot_instance else 300

        if security_name and (subnet_id or subnet_ids):
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
            if isinstance(valid_ami_owners, int):
                valid_ami_owners = (valid_ami_owners,)
            else:
                for element in valid_ami_owners:
                    if not isinstance(element, int):
                        raise ValueError(
                            'valid_ami_owners should be int or iterable '
                            'of ints', element)
        if valid_ami_location_regex is not None:
            if not isinstance(valid_ami_location_regex, str):
                raise ValueError(
                    'valid_ami_location_regex should be a string')
            else:
                # pre-compile the regex
                valid_ami_location_regex = re.compile(valid_ami_location_regex)
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
            self.placement = '{}{}'.format(region, placement)
        else:
            self.placement = None
        if identifier is None:
            assert secret_identifier is None, (
                'supply both or neither of identifier, secret_identifier')
            if aws_id_file_path is None:
                home = os.environ['HOME']
                default_path = os.path.join(home, '.workers', 'aws_id')
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
            # Using workers.vpc_addresses.filter(PublicIps=[elastic_ip]) throws a
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

        self.security_group_ids = security_group_ids
        self.classic_security_groups = [
            self.security_name] if self.security_name else None
        self.instance_profile_name = instance_profile_name
        self.tags = tags
        self.block_device_map = self.create_block_device_mapping(
            block_device_map) if block_device_map else None
        if self.spot_instance:
            if self.placement is None and len(self.subnet_ids) == 1:
                self.placement = self.ec2.Subnet(self.subnet_ids[0]).availability_zone
                self.az_to_subnet = {self.placement: self.subnet_ids[0]}
            if len(self.subnet_ids) == 0:
                self.spot_zones = [self.placement]
            else:
                self.spot_zones = []
                self.az_to_subnet = {}
                for i in self.subnet_ids:
                    az = self.ec2.Subnet(i).availability_zone
                    self.spot_zones.append(az)
                    self.az_to_subnet[az] = i
        else:
            self.subnet_id = subnet_id
            if self.placement is None:
                self.placement = self.ec2.Subnet(self.subnet_id).availability_zone

    def _start_instance(self):
        image = self.get_image()
        launch_opts = dict(
            ImageId=image.id, KeyName=self.keypair_name,
            SecurityGroups=self.classic_security_groups,
            InstanceType=self.instance_type, UserData=self.user_data,
            Placement=self._remove_none_opts(
                AvailabilityZone=self.placement,
            ),
            MinCount=1, MaxCount=1,
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
        return None

    def _bid_price_from_spot_price_history(self):
        timestamp_yesterday = time.gmtime(int(time.time() - 86400))
        spot_history_starttime = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', timestamp_yesterday)
        ret = self.ec2.meta.client.describe_spot_price_history(
            Filters=[dict(Name='availability-zone', Values=self.spot_zones)],
            StartTime=spot_history_starttime,
            ProductDescriptions=[self.product_description],
            InstanceTypes=self.instance_types)
        if 'SpotPriceHistory' not in ret:
            return {}
        info = {}
        for entry in ret['SpotPriceHistory']:
            k = '{}:{}'.format(entry['AvailabilityZone'], entry['InstanceType'])
            price = float(entry['SpotPrice'])
            if k in info:
                info[k].append(price)
            else:
                info[k] = [price]
        bid_prices = {}
        for k, pricelist in info.items():
            bid = (sum(pricelist)/len(pricelist)) * self.price_multiplier
            if self.max_spot_price is not None and bid > self.max_spot_price:
                bid = self.max_spot_price
            bid_prices[k] = bid

        return bid_prices

    def _request_spot_instance(self):
        if self.price_multiplier is None:
            bid_prices = {itype: self.max_spot_price for itype in self.instance_types}
        else:
            bid_prices = self._bid_price_from_spot_price_history()
        for k, bid_price in sorted(bid_prices.items(), key=lambda x: x[1]):
            zone, instance_type = k.split(':')
            subnet_id = self.az_to_subnet[zone]
            log.msg('%s %s requesting spot instance %s in zone %s with price %0.4f' %
                    (self.__class__.__name__, self.workername, instance_type, zone, bid_price))
            reservations = self.ec2.meta.client.request_spot_instances(
                SpotPrice=str(bid_price),
                LaunchSpecification=self._remove_none_opts(
                    ImageId=self.ami,
                    KeyName=self.keypair_name,
                    SecurityGroups=self.classic_security_groups,
                    UserData=(base64.b64encode(bytes(self.user_data, 'utf-8')).decode('ascii')
                              if self.user_data else None),
                    InstanceType=instance_type,
                    Placement=self._remove_none_opts(
                        AvailabilityZone=zone,
                    ),
                    NetworkInterfaces=[{'AssociatePublicIpAddress': True,
                                        'DeviceIndex': 0,
                                        'Groups': self.security_group_ids,
                                        'SubnetId': subnet_id}],
                    BlockDeviceMappings=self.block_device_map,
                    IamInstanceProfile=self._remove_none_opts(
                        Name=self.instance_profile_name,
                    )
                ),
                ValidUntil=datetime.datetime.now() + datetime.timedelta(seconds=60)
            )
            reservation = reservations['SpotInstanceRequests'][0]
            spotWaiter = self.ec2.meta.client.get_waiter('spot_instance_request_fulfilled')
            try:
                spotWaiter.wait(SpotInstanceRequestIds=[reservation['SpotInstanceRequestId']],
                                WaiterConfig={'Delay': 5, 'MaxAttempts': 6})
            except botocore.exceptions.WaiterError:
                pass
            try:
                request, success = self._wait_for_request(reservation)
                if not success:
                    log.msg('{} {} spot request not successful'.format(
                        self.__class__.__name__, self.workername))
                    continue
            except LatentWorkerFailedToSubstantiate as e:
                reqid, status = e.args
                log.msg('{} {} spot request {} rejected: {}'.format(
                    self.__class__.__name__, self.workername, reqid, status))
                continue

            instance_id = request['InstanceId']
            self.placement = zone
            self.subnet_id = subnet_id
            self.instance = self.ec2.Instance(instance_id)
            image = self.get_image()
            instance_id, start_time = self._wait_for_instance()
            return instance_id, image.id, start_time
        raise LatentWorkerFailedToSubstantiate(self.workername, "exhausted instance types")


def active_slots(w):
    return [wfb for wfb in w.workerforbuilders.values() if wfb.isBusy()]


def nextEC2Worker(bldr, wfbs, br):
    """
    Called by BuildRequestDistributor to identify a worker to queue
    a build to. Instead of using the default random selection provided
    by buildbot, choose using the following algorithm.
        - Prefer non-latent workers over latent workers
        - Prefer running latent workers with available slots over non-running (even pending) ones.
        - Prefer pending latent workers over those that are shut down or shutting down.
        - Sort preferred latent workers based on number of available slots
    :param bldr: Builder object
    :param wfbs: list of WorkerForBuilder objects
    :param br: BuildRequest object
    :return: WorkerForBuilder object
    """
    from buildbot.worker.ec2 import TERMINATED, PENDING, RUNNING
    log.msg('nextEC2Worker: %d WorkerForBuilders: %s' % (len(wfbs),
                                                         ','.join([wfb.worker.name for wfb in wfbs])))
    candidates = [wfb for wfb in wfbs if wfb.isAvailable()]
    log.msg('nextEC2Worker: %d candidates: %s' % (len(candidates),
                                                  ','.join([wfb.worker.name for wfb in candidates])))
    wdict = {}
    realworkers = []
    for wfb in candidates:
        if wfb.worker is not None and isinstance(wfb.worker, MyEC2LatentWorker):
            if wfb.worker.instance:
                statename = wfb.worker.instance.state['Name']
            else:
                statename = TERMINATED
            if statename in [PENDING, RUNNING]:
                if wfb.worker.max_builds:
                    slots = wfb.worker.max_builds - len(active_slots(wfb.worker))
                    # If this worker is running and has available worker slots, bump
                    # its score so it gets chosen first.
                    if slots > 0 and statename == RUNNING:
                        slots += 100
                    log.msg('nextEC2Worker:   worker %s score=%d' % (wfb.worker.name, slots))
                    if slots in wdict.keys():
                        wdict[slots].append(wfb)
                    else:
                        wdict[slots] = [wfb]
            else:
                if 0 in wdict.keys():
                    wdict[0].append(wfb)
                else:
                    wdict[0] = [wfb]
        else:
            log.msg('nextEC2Worker:   non-latent worker: %s' % wfb.worker.name)
            realworkers.append(wfb)
    if len(realworkers) > 0:
        log.msg('nextEC2Worker: chose (non-latent): %s' % realworkers[0].worker.name)
        return realworkers[0]
    best = sorted(wdict.keys(), reverse=True)[0]
    log.msg('nextEC2Worker: chose: %s (score=%d)' % (wdict[best][0].worker.name, best))
    return wdict[best][0]
