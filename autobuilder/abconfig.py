"""
Autobuilder configuration class.
"""
import os
from buildbot.plugins import changes, schedulers, util, worker
from buildbot.www.hooks.github import GitHubEventHandler
from buildbot.config import BuilderConfig
from autobuilder import factory

ABCFG_DICT = {}
DEFAULT_BLDTYPES = ['ci', 'snapshot', 'release']

class Buildtype(object):
    def __init__(self, name, build_sdk=False, install_sdk=False,
                 sdk_root=None, current_symlink=False, defaulttype=False,
                 production_release=False):
        self.name = name
        self.build_sdk = build_sdk
        self.install_sdk = install_sdk
        self.sdk_root = sdk_root
        self.current_symlink = current_symlink
        self.defaulttype = defaulttype
        self.production_release = production_release

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
    def __init__(self, name, reponame, branch, email, path, dldir, ssmirror,
                 targets, sdkmachines=None,
                 host_oses=None, setup_script='./setup-env', repotimer=300,
                 artifacts=None,
                 sstate_mirrorvar='SSTATE_MIRRORS = "file://.* file://%s/PATH"',
                 dl_mirrorvar=None,
                 buildtypes=None, buildnum_template='DISTRO_BUILDNUM = "-%s"',
                 release_buildname_variable='DISTRO_BUILDNAME',
                 dl_mirror='file:///dummy/no/such/path'):
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

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame, repository=repos[self.reponame].uri)]

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
    def __init__(self, instance_type, ami, keypair, secgroup_ids,
                 region=None, subnet=None, elastic_ip=None, tags=None,
                 scratchvolparams={'name': '/dev/xvdf', 'size': 150,
                                   'type': 'standard', 'iops': None}):
        self.instance_type = instance_type
        self.ami = ami
        self.keypair = keypair
        self.region = region
        self.secgroup_ids = secgroup_ids
        self.subnet = subnet
        self.elastic_ip = elastic_ip
        self.tags = tags
        self.scratchvolparams = scratchvolparams


class AutobuilderEC2Worker(AutobuilderWorker):
    def __init__(self, name, password, ec2params, conftext=None):
        AutobuilderWorker.__init__(self, name, password, conftext)
        self.ec2params = ec2params
        self.ec2tags = ec2params.tags
        if self.ec2tags:
            if 'Name' not in self.ec2tags:
                self.ec2tags['Name'] = self.name
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
                { 'DeviceName': svp['name'], 'Ebs': ebs }
            ]
                                         

def get_project_for_url(repo_url, default_if_not_found=None):
    for abcfg in ABCFG_DICT:
        proj = ABCFG_DICT[abcfg].project_from_url(repo_url)
        if proj is not None:
            return proj
    return default_if_not_found

def codebasemap_from_github_payload(payload):
    return get_project_for_url(payload['repository']['url'])


class AutobuilderGithubEventHandler(GitHubEventHandler):

    # noinspection PyMissingConstructor
    def __init__(self, secret, strict, codebase=None):
        if codebase is None:
            codebase = codebasemap_from_github_payload
        GitHubEventHandler.__init__(self, secret, strict, codebase)


    def handle_push(self, payload):
        # This field is unused:
        user = None
        # user = payload['pusher']['name']
        repo = payload['repository']['name']
        repo_url = payload['repository']['url']
        # NOTE: what would be a reasonable value for project?
        # project = request.args.get('project', [''])[0]
        project = get_project_for_url(repo_url,
                                      default_if_not_found=payload['repository']['full_name'])

        changes = self._process_change(payload, user, repo, repo_url, project)

        log.msg("Received %d changes from github" % len(changes))

        return changes, 'git'

class AutobuilderConfig(object):
    def __init__(self, name, workers, controllers,
                 repos, distros, ec2workers=None):
        if name in ABCFG_DICT:
            raise RuntimeError('Autobuilder config %s already exists' % name)
        self.name = name
        ostypes = set()
        self.workers = []
        self.worker_cfgs = {}
        wnames = {}
        controllernames = []
        if workers:
            ostypes |= set(workers.keys())
            for ostype in workers:
                if ostype not in wnames.keys():
                    wnames[ostype] = []
                for w in workers[ostype]:
                    self.workers.append(worker.Worker(w.name, w.password, max_builds=1))
                    self.worker_cfgs[w.name] = w
                    wnames[ostype].append(w.name)
        if ec2workers:
            ostypes |= set(ec2workers.keys())
            for ostype in ec2workers:
                if ostype not in wnames.keys():
                    wnames[ostype] = []
                for w in ec2workers[ostype]:
                    self.workers.append(worker.EC2LatentWorker(name=w.name,
                                                               password=w.password,
                                                               max_builds=1,
                                                               instance_type=w.ec2params.instance_type,
                                                               ami=w.ec2params.ami,
                                                               keypair_name=w.ec2params.keypair,
                                                               security_group_ids=w.ec2params.secgroup_ids,
                                                               region=w.ec2params.region,
                                                               subnet_id=w.ec2params.subnet,
                                                               user_data='WORKERNAME="%s"\nWORKERSECRET="%s"\nMASTER="%s"\n' %
                                                                         (w.name, w.password, os.getenv('MASTER_IP_ADDRESS')),
                                                               elastic_ip=w.ec2params.elastic_ip,
                                                               tags=w.ec2tags,
                                                               block_device_map=w.ec2_dev_mapping))
                    self.worker_cfgs[w.name] = w
                    wnames[ostype].append(w.name)

        for c in controllers:
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
        ABCFG_DICT[name] = self

    def codebase_generator(self, change_dict):
        return self.codebasemap[change_dict['repository']]

    def project_from_url(self, repo_url):
        try:
            return self.repos[self.codebasemap[repo_url]].project
        except KeyError:
            return None

    @property
    def change_sources(self):
        return [GitPoller(repourl=self.repos[r].uri,
                          workdir='gitpoller-' + self.repos[r].name,
                          branches=[d.branch for d in self.distros
                                     if d.reponame == r],
                          pollinterval=self.repos[r].pollinterval,
                          pollAtLaunch=True, project=self.repos[r].project)
                for r in self.repos if self.repos[r].pollinterval]

    @property
    def schedulers(self):
        s = []
        for d in self.distros:
            md_filter = util.ChangeFilter(project=self.repos[d.reponame].project,
                                          branch=d.branch, codebase=d.reponame)
            s.append(schedulers.SingleBranchScheduler(name=d.name,
                                           change_filter=md_filter,
                                           treeStableTimer=d.repotimer,
                                           properties={'buildtype': d.default_buildtype},
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
        return s

    @property
    def builders(self):
        b = []
        for d in self.distros:
            props = {'sstate_mirror': d.sstate_mirror,
                     'sstate_mirrorvar': d.sstate_mirrorvar,
                     'dl_mirrorvar': d.dl_mirrorvar or "",
                     'dl_mirror': d.dl_mirror,
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
            b.append(BuilderConfig(name=d.name,
                                   workernames=self.controller_names,
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
