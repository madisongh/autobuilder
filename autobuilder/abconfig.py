"""
Autobuilder configuration class.
"""
import os
from random import SystemRandom
from twisted.python import log
from buildbot.plugins import changes, schedulers, util, worker
from buildbot.www.hooks.github import GitHubEventHandler
from buildbot.config import BuilderConfig
from buildbot.worker import AbstractLatentWorker
from autobuilder import factory, settings
import boto3
import botocore
from botocore.client import ClientError

DEFAULT_BLDTYPES = ['ci', 'no-sstate', 'snapshot', 'release']
RNG = SystemRandom()
default_svp = {'name': '/dev/xvdf', 'size': 200,
               'type': 'standard', 'iops': None}


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


class Buildtype(object):
    def __init__(self, name, build_sdk=False, install_sdk=False,
                 sdk_root=None, current_symlink=False, defaulttype=False,
                 production_release=False, disable_sstate=False):
        self.name = name
        self.build_sdk = build_sdk
        self.install_sdk = install_sdk
        self.sdk_root = sdk_root
        self.current_symlink = current_symlink
        self.defaulttype = defaulttype
        self.production_release = production_release
        self.disable_sstate = disable_sstate


class Repo(object):
    def __init__(self, name, uri, pollinterval=None, project=None,
                 submodules=False):
        self.name = name
        self.uri = uri
        self.pollinterval = pollinterval
        self.project = project or name
        self.submodules = submodules


class TargetImageSet(object):
    def __init__(self, name, images=None, sdkimages=None):
        self.name = name
        if images is None and sdkimages is None:
            raise RuntimeError('No images or SDK images defined for %s' %
                               name)
        self.images = images
        self.sdkimages = sdkimages


class Distro(object):
    def __init__(self, name, reponame, branch, email, path,
                 dldir=None, ssmirror=None,
                 targets=None, sdkmachines=None,
                 host_oses=None, setup_script='./setup-env', repotimer=300,
                 artifacts=None,
                 sstate_mirrorvar='SSTATE_MIRRORS = "file://.* file://%s/PATH"',
                 dl_mirrorvar=None,
                 controllers=None,
                 buildtypes=None, buildnum_template='DISTRO_BUILDNUM = "-%s"',
                 release_buildname_variable='DISTRO_BUILDNAME',
                 dl_mirror=None,
                 skip_sstate_update=False,
                 clean_downloads=True,
                 weekly_type=None,
                 push_type='__default__'):
        self.name = name
        self.reponame = reponame
        self.branch = branch
        self.email = email
        self.artifacts_path = path
        self.dl_dir = dldir
        self.sstate_mirror = ssmirror
        self.targets = targets
        self.sdkmachines = sdkmachines
        self.host_oses = host_oses
        self.setup_script = setup_script
        self.repotimer = repotimer
        self.artifacts = artifacts
        self.sstate_mirrorvar = sstate_mirrorvar
        self.dl_mirrorvar = dl_mirrorvar
        self.dl_mirror = dl_mirror
        self.skip_sstate_update = skip_sstate_update
        self.clean_downloads = clean_downloads
        self.controllers = controllers
        self.buildnum_template = buildnum_template
        self.release_buildname_variable = release_buildname_variable
        self.buildtypes = buildtypes
        if buildtypes is None:
            self.buildtypes = [Buildtype(bt) for bt in DEFAULT_BLDTYPES]
            self.buildtypes[0].defaulttype = True
        self.btdict = {bt.name: bt for bt in self.buildtypes}
        defaultlist = [bt.name for bt in self.buildtypes if bt.defaulttype]
        if len(defaultlist) != 1:
            raise RuntimeError('Must set exactly one default build type for %s' % self.name)
        self.default_buildtype = defaultlist[0]
        if weekly_type is not None and weekly_type not in self.btdict.keys():
            raise RuntimeError('Weekly build type for %s set to unknown type: %s' % (self.name, weekly_type))
        self.weekly_type = weekly_type
        if push_type:
            self.push_type = push_type if push_type != '__default__' else self.default_buildtype
        else:
            self.push_type = None

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame,
                                       repository=util.FixedParameter(name='repository',
                                                                      default=repos[self.reponame].uri),
                                       branch=util.FixedParameter(name='branch', default=self.branch),
                                       project=util.FixedParameter(name='project',
                                                                   default=repos[self.reponame].project))]

    def set_host_oses(self, default_oses):
        if self.host_oses is None:
            self.host_oses = default_oses


class AutobuilderWorker(object):
    def __init__(self, name, password, conftext=None):
        self.name = name
        self.password = password
        self.conftext = conftext


class AutobuilderController(AutobuilderWorker):
    def __init__(self, name, password):
        AutobuilderWorker.__init__(self, name, password)


class EC2Params(object):
    def __init__(self, instance_type, ami, secgroup_ids, keypair=None,
                 region=None, subnet=None, elastic_ip=None, tags=None,
                 scratchvolparams=default_svp, instance_profile_name=None):
        self.instance_type = instance_type
        self.ami = ami
        self.keypair = keypair
        self.region = region
        self.secgroup_ids = secgroup_ids
        self.subnet = subnet
        self.elastic_ip = elastic_ip
        self.tags = tags
        self.scratchvolparams = scratchvolparams
        self.instance_profile_name = instance_profile_name


class AutobuilderEC2Worker(AutobuilderWorker):
    master_ip_address = os.getenv('MASTER_IP_ADDRESS')

    def __init__(self, name, password, ec2params, conftext=None):
        AutobuilderWorker.__init__(self, name, password, conftext)
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
            if svp['type'] == 'io1':
                if svp['iops']:
                    ebs['Iops'] = svp['iops']
                else:
                    ebs['Iops'] = 1000
            self.ec2_dev_mapping = [
                {'DeviceName': svp['name'], 'Ebs': ebs}
            ]

    def userdata(self):
        return 'WORKERNAME="{}"\n'.format(self.name) + \
               'WORKERSECRET="{}"\n'.format(self.password) + \
               'MASTER="{}"\n'.format(self.master_ip_address)


def get_project_for_url(repo_url, default_if_not_found=None):
    for abcfg in settings.settings_dict():
        proj = settings.get_config_for_builder(abcfg).project_from_url(repo_url)
        if proj is not None:
            return proj
    return default_if_not_found


def codebasemap_from_github_payload(payload):
    return get_project_for_url(payload['repository']['html_url'])


class AutobuilderGithubEventHandler(GitHubEventHandler):
    # noinspection PyMissingConstructor
    def __init__(self, secret, strict, codebase=None, **kwargs):
        if codebase is None:
            codebase = codebasemap_from_github_payload
        GitHubEventHandler.__init__(self, secret, strict, codebase, **kwargs)

    def handle_push(self, payload, event):
        # This field is unused:
        user = None
        # user = payload['pusher']['name']
        repo = payload['repository']['name']
        repo_url = payload['repository']['html_url']
        # NOTE: what would be a reasonable value for project?
        # project = request.args.get('project', [''])[0]
        project = get_project_for_url(repo_url,
                                      default_if_not_found=payload['repository']['full_name'])

        properties = self.extractProperties(payload)
        changeset = self._process_change(payload, user, repo, repo_url, project,
                                         event, properties)
        for ch in changeset:
            ch['category'] = 'push'

        log.msg("Received {} changes from github".format(len(changeset)))

        return changeset, 'git'


class AutobuilderConfig(object):
    def __init__(self, name, workers, controllers,
                 repos, distros):
        if name in settings.settings_dict():
            raise RuntimeError('Autobuilder config {} already exists'.format(name))
        self.name = name
        ostypes = set()
        self.workers = []
        self.worker_cfgs = {}
        wnames = {}
        controllernames = []
        ostypes |= set(workers.keys())
        for ostype in workers:
            if ostype not in wnames.keys():
                wnames[ostype] = []
            for w in workers[ostype]:
                if isinstance(w, AutobuilderEC2Worker):
                    self.workers.append(MyEC2LatentWorker(name=w.name,
                                                          password=w.password,
                                                          max_builds=1,
                                                          instance_type=w.ec2params.instance_type,
                                                          ami=w.ec2params.ami,
                                                          keypair_name=w.ec2params.keypair,
                                                          instance_profile_name=w.ec2params.instance_profile_name,
                                                          security_group_ids=w.ec2params.secgroup_ids,
                                                          region=w.ec2params.region,
                                                          subnet_id=w.ec2params.subnet,
                                                          user_data=w.userdata(),
                                                          elastic_ip=w.ec2params.elastic_ip,
                                                          tags=w.ec2tags,
                                                          block_device_map=w.ec2_dev_mapping))
                else:
                    self.workers.append(worker.Worker(w.name, w.password, max_builds=1))
                self.worker_cfgs[w.name] = w
                wnames[ostype].append(w.name)

        for c in controllers:
            if isinstance(c, AutobuilderEC2Worker):
                self.workers.append(MyEC2LatentWorker(name=c.name,
                                                      password=c.password,
                                                      max_builds=1,
                                                      instance_type=c.ec2params.instance_type,
                                                      ami=c.ec2params.ami,
                                                      keypair_name=c.ec2params.keypair,
                                                      instance_profile_name=c.ec2params.instance_profile_name,
                                                      security_group_ids=c.ec2params.secgroup_ids,
                                                      region=c.ec2params.region,
                                                      subnet_id=c.ec2params.subnet,
                                                      user_data=c.userdata(),
                                                      elastic_ip=c.ec2params.elastic_ip,
                                                      tags=c.ec2tags,
                                                      block_device_map=c.ec2_dev_mapping))
            else:
                self.workers.append(worker.Worker(c.name, c.password, max_builds=1))
            # controllers aren't normal build workers
            self.worker_cfgs[c.name] = None
            controllernames.append(c.name)

        self.ostypes = sorted(ostypes)
        self.worker_names = {}
        for ostype in self.ostypes:
            self.worker_names[ostype] = sorted(wnames[ostype])
        self.controller_names = sorted(controllernames)

        self.repos = repos
        self.distros = distros
        self.distrodict = {d.name: d for d in self.distros}
        for d in self.distros:
            d.set_host_oses(self.ostypes)
        self.codebasemap = {self.repos[r].uri: r for r in self.repos}
        settings.set_config_for_builder(name, self)

    def codebase_generator(self, change_dict):
        return self.codebasemap[change_dict['repository']]

    def project_from_url(self, repo_url):
        try:
            return self.repos[self.codebasemap[repo_url]].project
        except KeyError:
            return None

    @property
    def change_sources(self):
        pollers = []
        for r in self.repos:
            if self.repos[r].pollinterval:
                branches = set()
                for d in self.distros:
                    if d.reponame == r and d.push_type:
                        branches.add(d.branch)
                pollers.append(changes.GitPoller(self.repos[r].uri,
                                                 workdir='gitpoller-' + self.repos[r].name,
                                                 branches=sort(branches),
                                                 category='push',
                                                 pollinterval=self.repos[r].pollinterval,
                                                 pollAtLaunch=True,
                                                 project=self.repos[r].project))
        return pollers

    @property
    def schedulers(self):
        s = []
        for d in self.distros:
            if d.push_type is not None:
                md_filter = util.ChangeFilter(project=self.repos[d.reponame].project,
                                              branch=d.branch, codebase=d.reponame,
                                              category='push')
                s.append(schedulers.SingleBranchScheduler(name=d.name,
                                                          change_filter=md_filter,
                                                          treeStableTimer=d.repotimer,
                                                          properties={'buildtype': d.push_type},
                                                          codebases=d.codebases(self.repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=[d.name]))
            for imgset in d.targets:
                name = d.name + '-' + imgset.name
                s += [schedulers.Triggerable(name=name + '-' + otype,
                                             codebases=d.codebases(self.repos),
                                             properties={'hostos': otype},
                                             builderNames=[name + '-' + otype])
                      for otype in d.host_oses]
            # noinspection PyTypeChecker
            forceprops = util.ChoiceStringParameter(name='buildtype',
                                                    label='Build type',
                                                    choices=[bt.name for bt in d.buildtypes],
                                                    default=d.default_buildtype)
            s.append(schedulers.ForceScheduler(name=d.name + '-force',
                                               codebases=d.codebaseparamlist(self.repos),
                                               properties=[forceprops],
                                               builderNames=[d.name]))
            if d.weekly_type is not None:
                slot = settings.get_weekly_slot()
                s.append(schedulers.Nightly(name=d.name + '-' + 'weekly',
                                            properties={'buildtype': d.weekly_type},
                                            codebases=d.codebases(self.repos),
                                            createAbsoluteSourceStamps=True,
                                            builderNames=[d.name],
                                            dayOfWeek=slot.dayOfWeek,
                                            hour=slot.hour,
                                            minute=slot.minute))
        return s

    @property
    def builders(self):
        b = []
        for d in self.distros:
            props = {'sstate_mirror': d.sstate_mirror,
                     'sstate_mirrorvar': d.sstate_mirrorvar,
                     'dl_mirrorvar': d.dl_mirrorvar or "",
                     'dl_mirror': d.dl_mirror,
                     'skip_sstate_update': 'yes' if d.skip_sstate_update else 'no',
                     'clean_downloads': 'yes' if d.clean_downloads else 'no',
                     'artifacts_path': d.artifacts_path,
                     'downloads_dir': d.dl_dir,
                     'project': self.repos[d.reponame].project,
                     'repourl': self.repos[d.reponame].uri,
                     'branch': d.branch,
                     'setup_script': d.setup_script,
                     'artifacts': ' '.join(d.artifacts),
                     'autobuilder': self.name,
                     'distro': d.name,
                     'buildnum_template': d.buildnum_template,
                     'release_buildname_variable': d.release_buildname_variable}
            if d.controllers is None:
                cnames = self.controller_names
            else:
                cnames = sorted([c for c in self.controller_names if c in d.controllers])
            b.append(BuilderConfig(name=d.name,
                                   workernames=cnames,
                                   properties=props.copy(),
                                   factory=factory.DistroBuild(d, self.repos)))
            repo = self.repos[d.reponame]
            for imgset in d.targets:
                b += [BuilderConfig(name=d.name + '-' + imgset.name + '-' + otype,
                                    workernames=self.worker_names[otype],
                                    properties=props.copy(),
                                    factory=factory.DistroImage(repourl=repo.uri,
                                                                submodules=repo.submodules,
                                                                branch=d.branch,
                                                                codebase=d.reponame,
                                                                imagedict=imgset.images,
                                                                sdkmachines=d.sdkmachines,
                                                                sdktargets=imgset.sdkimages))
                      for otype in d.host_oses]
        return b
