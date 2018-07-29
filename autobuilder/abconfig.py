"""
Autobuilder configuration class.
"""
import os
import string
import time
from random import SystemRandom
from twisted.python import log
from buildbot.plugins import changes, schedulers, util, worker
from buildbot.www.hooks.github import GitHubEventHandler
from buildbot.config import BuilderConfig
from autobuilder import factory, settings
from autobuilder.ec2 import MyEC2LatentWorker
from autobuilder import utils

DEFAULT_BLDTYPES = ['ci', 'no-sstate', 'snapshot', 'release']
RNG = SystemRandom()
default_svp = {'name': '/dev/xvdf', 'size': 200,
               'type': 'standard', 'iops': None}


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


class EC2Params(object):
    def __init__(self, instance_type, ami, secgroup_ids, keypair=None,
                 region=None, subnet=None, elastic_ip=None, tags=None,
                 scratchvol=False, scratchvol_params=None,
                 instance_profile_name=None):
        self.instance_type = instance_type
        self.ami = ami
        self.keypair = keypair
        self.region = region
        self.secgroup_ids = secgroup_ids
        self.subnet = subnet
        self.elastic_ip = elastic_ip
        self.tags = tags
        if scratchvol:
            self.scratchvolparams = scratchvol_params or default_svp
        else:
            self.scratchvolparams = None
        self.instance_profile_name = instance_profile_name


class AutobuilderEC2Worker(AutobuilderWorker):
    master_ip_address = os.getenv('MASTER_IP_ADDRESS')

    def __init__(self, name, password, ec2params, conftext=None):
        if not password:
            password = ''.join(RNG.choice(string.ascii_letters + string.digits) for _ in range(16))
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
    def __init__(self, name, workers, repos, distros):
        if name in settings.settings_dict():
            raise RuntimeError('Autobuilder config {} already exists'.format(name))
        self.name = name
        ostypes = set()
        self.workers = []
        self.worker_cfgs = {}
        wnames = {}
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

        self.ostypes = sorted(ostypes)
        self.worker_names = {}
        for ostype in self.ostypes:
            self.worker_names[ostype] = sorted(wnames[ostype])

        self.repos = repos
        self.distros = distros
        self.distrodict = {d.name: d for d in self.distros}
        for d in self.distros:
            d.set_host_oses(self.ostypes)
            d.builder_names = [d.name + '-' + imgset.name + '-' + otype for imgset in d.targets for otype in
                               d.host_oses]
        all_builder_names = []
        for d in self.distros:
            all_builder_names += d.builder_names
        self.all_builder_names = sorted(all_builder_names)
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
                props = {'buildtype': d.push_type,
                         'datestamp': str(time.strftime("%Y%m%d"))}
                s.append(schedulers.SingleBranchScheduler(name=d.name,
                                                          change_filter=md_filter,
                                                          treeStableTimer=d.repotimer,
                                                          properties=props,
                                                          codebases=d.codebases(self.repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=d.builder_names))
            # noinspection PyTypeChecker
            forceprops = [util.ChoiceStringParameter(name='buildtype',
                                                     label='Build type',
                                                     choices=[bt.name for bt in d.buildtypes],
                                                     default=d.default_buildtype),
                          util.FixedParameter(name='datestamp',
                                              default=str(time.strftime("%Y%m%d")))]
            s.append(schedulers.ForceScheduler(name=d.name + '-force',
                                               codebases=d.codebaseparamlist(self.repos),
                                               properties=forceprops,
                                               builderNames=d.builder_names))
            if d.weekly_type is not None:
                slot = settings.get_weekly_slot()
                s.append(schedulers.Nightly(name=d.name + '-' + 'weekly',
                                            properties={'buildtype': d.weekly_type,
                                                        'datestamp': str(time.strftime("%Y%m%d"))},
                                            codebases=d.codebases(self.repos),
                                            createAbsoluteSourceStamps=True,
                                            builderNames=d.builder_names,
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
            repo = self.repos[d.reponame]
            b += [BuilderConfig(name=d.name + '-' + imgset.name + '-' + otype,
                                workernames=self.worker_names[otype],
                                properties=utils.dict_merge(props,
                                                            {'primary_hostos': otype == d.host_oses[0],
                                                             'save_artifacts': otype == d.host_oses[0]}),
                                factory=factory.DistroImage(repourl=repo.uri,
                                                            submodules=repo.submodules,
                                                            branch=d.branch,
                                                            codebase=d.reponame,
                                                            imagedict=imgset.images,
                                                            sdkmachines=d.sdkmachines,
                                                            sdktargets=imgset.sdkimages))
                    for imgset in d.targets for otype in d.host_oses]
        return b
