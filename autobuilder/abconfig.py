"""
Autobuilder configuration class.
"""
import os
import string
import logging
import socket
from random import SystemRandom
from dateutil.parser import parse as dateparse
from twisted.internet import defer
from twisted.python import log
from buildbot.plugins import changes, schedulers, util, worker
from buildbot.www.hooks.github import GitHubEventHandler
from buildbot.config import BuilderConfig
import jinja2
import urllib.parse
from autobuilder import factory, settings
from autobuilder.ec2 import MyEC2LatentWorker

RNG = SystemRandom()
default_svp = {'name': '/dev/xvdf', 'size': 200,
               'type': 'standard', 'iops': None}


class Buildtype(object):
    def __init__(self, name, current_symlink=False, defaulttype=False,
                 pullrequesttype=False, production_release=False,
                 disable_sstate=False, keep_going=False, extra_config=None):
        self.name = name
        self.keep_going = keep_going
        self.current_symlink = current_symlink
        self.defaulttype = defaulttype
        self.pullrequesttype = pullrequesttype
        self.production_release = production_release
        self.disable_sstate = disable_sstate
        if extra_config:
            self.extra_config = [extra_config] if isinstance(extra_config, str) else extra_config
        else:
            self.extra_config = []


DEFAULT_BLDTYPES = [Buildtype('ci', defaulttype=True),
                    Buildtype('no-sstate', disable_sstate=True,
                              extra_config=['SSTATE_MIRRORS_forcevariable = ""']),
                    Buildtype('release', production_release=True),
                    Buildtype('pr', pullrequesttype=True,
                              extra_config=['INHERIT_remove = "buildhistory"'])]


class Repo(object):
    def __init__(self, name, uri, pollinterval=None,
                 submodules=False):
        self.name = name
        self.uri = uri
        self.pollinterval = pollinterval
        self.submodules = submodules


class ImageSpec(object):
    def __init__(self, name=None, machine=None, sdkmachine=None):
        self.name = name
        self.machine = machine
        self.sdkmachine = sdkmachine
        if not machine and not sdkmachine:
            raise ValueError("ImageSpec with no MACHINE or SDKMACHINE setting")
        self.mcname = machine if machine else 'none-' + sdkmachine
        self.is_sdk = False


class TargetImage(ImageSpec):
    def __init__(self, machine, args, name=None):
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        if not name:
            name = machine + ':' + '_'.join([a for a in self.args if not a.startswith('-')])
        super().__init__(name, machine=machine)


class SdkImage(ImageSpec):
    def __init__(self, machine, sdkmachine, args, name=None):
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        if not name:
            if machine:
                name = 'SDK_%s:%s:%s' % (sdkmachine, machine,
                                         '_'.join([a for a in self.args if not a.startswith('-')]))
            else:
                name = 'SDK_%s:%s' % (sdkmachine, '_'.join([a for a in self.args if not a.startswith('-')]))
        super().__init__(name, machine, sdkmachine)
        self.is_sdk = True


class TargetImageSet(object):
    def __init__(self, name, imagespecs=None, multiconfig=False, distro=None, artifacts=None):
        self.name = name
        self.distro = distro
        self.multiconfig = multiconfig
        self.artifacts = artifacts
        if imagespecs is None:
            raise RuntimeError('No images defined for %s' % name)
        self.imagespecs = imagespecs


class Layer(object):
    def __init__(self, name, reponame, pokyurl, branches, email,
                 repotimer=300,
                 pullrequests=False,
                 layerdir=None,
                 machines=None,
                 extra_config=None,
                 extra_env=None,
                 extra_options=None):
        self.name = name
        self.reponame = reponame
        self.pokyurl = pokyurl
        self.branches = branches
        self.email = email
        self._layerdir = layerdir
        self.repotimer = repotimer
        self.pullrequests = pullrequests
        self.machines = machines or ['qemux86']
        self.extra_config = extra_config
        self.extra_env = extra_env
        self.extra_options = extra_options

    def layerdir(self, url):
        if self._layerdir:
            return self._layerdir
        return os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0]

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame,
                                       repository=util.FixedParameter(name='repository',
                                                                      default=repos[self.reponame].uri),
                                       branch=util.ChoiceStringParameter(name='branch',
                                                                         choices=self.branches,
                                                                         default=self.branches[0]))]


class Distro(object):
    def __init__(self, name, reponame, branch, email, path,
                 targets=None,
                 setup_script='./setup-env',
                 repotimer=300,
                 artifacts=None,
                 buildtypes=None,
                 weekly_type=None,
                 push_type='__default__',
                 triggerable=False,
                 triggers=None,
                 pullrequest_type=None,
                 extra_config=None,
                 extra_env=None,
                 parallel_builders=False):
        self.name = name
        self.reponame = reponame
        self.branch = branch
        self.email = email
        self.artifacts_path = path
        self.targets = targets
        self.setup_script = setup_script
        self.repotimer = repotimer
        self.artifacts = artifacts or []
        self.triggerable = triggerable
        self.triggers = triggers
        self.buildtypes = buildtypes
        self.buildtypes = buildtypes or DEFAULT_BLDTYPES
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
        if pullrequest_type:
            prtypelist = [bt.name for bt in self.buildtypes if bt.pullrequesttype]
            if len(prtypelist) != 1:
                raise RuntimeError('Must set exactly one PR build type for %s' % self.name)
            self.pullrequest_type = prtypelist[0]
        else:
            self.pullrequest_type = None
        if extra_config:
            self.extra_config = [extra_config] if isinstance(extra_config, str) else extra_config
        else:
            self.extra_config = []
        self.extra_env = extra_env
        self.parallel_builders = parallel_builders

    def codebases(self, repos):
        cbdict = {self.reponame: {'repository': repos[self.reponame].uri}}
        return cbdict

    def codebaseparamlist(self, repos):
        return [util.CodebaseParameter(codebase=self.reponame,
                                       repository=util.FixedParameter(name='repository',
                                                                      default=repos[self.reponame].uri),
                                       branch=util.FixedParameter(name='branch', default=self.branch))]


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
                userdata_template_dir = os.path.join(os.path.dirname(__file__), "templates")
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


def get_project_for_url(repo_url, branch):
    for abcfg in settings.settings_dict():
        cfg = settings.get_config_for_builder(abcfg)
        try:
            reponame = cfg.codebasemap[repo_url]
            for distro in cfg.distros:
                if distro.reponame == reponame and distro.branch == branch:
                    log.msg('Found distro {} for repo {} and branch {}'.format(distro.name, reponame, branch))
                    if distro.push_type:
                        log.msg('Distro {} wants pushes'.format(distro.name))
                        return distro.name
        except KeyError:
            pass
    return None


def codebasemap_from_github_payload(payload):
    if 'pull_request' in payload:
        url = payload['pull_request']['base']['repo']['html_url']
    else:
        url = payload['repository']['html_url']
    reponame = ''
    for abcfg in settings.settings_dict():
        try:
            reponame = settings.get_config_for_builder(abcfg).codebasemap[url]
            break
        except KeyError:
            pass
    return reponame


def something_wants_pullrequests(payload):
    if 'pull_request' not in payload:
        log.msg('something_wants_pullrequests called for a non-PR?')
        return False
    url = payload['pull_request']['base']['repo']['html_url']
    basebranch = payload['pull_request']['base']['ref']
    for abcfg in settings.settings_dict():
        cfg = settings.get_config_for_builder(abcfg)
        try:
            reponame = cfg.codebasemap[url]
            for layer in cfg.layers:
                if layer.reponame == reponame and basebranch in layer.branches:
                    log.msg('Found layer {} for repo {} and branch {}'.format(layer.name, reponame, basebranch))
                    return layer.pullrequests
            for distro in cfg.distros:
                if distro.reponame == reponame and distro.branch == basebranch:
                    log.msg('Found distro {} for repo {} and branch {}'.format(distro.name, reponame, basebranch))
                    if distro.pullrequest_type:
                        log.msg('Distro {} wants pull requests'.format(distro.name))
                        return True
        except KeyError:
            pass
    log.msg('No distro or layer found for url {}, base branch {}'.format(url, basebranch))
    return False


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
        ref = payload['ref']
        if not ref.startswith('refs/heads/'):
            log.msg('Ignoring non-branch push (ref: {})'.format(ref))
            return [], 'git'
        branch = ref.split('/')[-1]
        project = get_project_for_url(repo_url, branch)
        if project is None:
            return [], 'git'

        properties = self.extractProperties(payload)
        changeset = self._process_change(payload, user, repo, repo_url, project,
                                         event, properties)
        for ch in changeset:
            ch['category'] = 'push'

        log.msg("Received {} changes from github".format(len(changeset)))

        return changeset, 'git'

    @defer.inlineCallbacks
    def handle_pull_request(self, payload, event):
        pr_changes = []
        number = payload['number']
        refname = 'refs/pull/{}/{}'.format(number, self.pullrequest_ref)
        commits = payload['pull_request']['commits']
        title = payload['pull_request']['title']
        comments = payload['pull_request']['body']
        repo_full_name = payload['repository']['full_name']
        head_sha = payload['pull_request']['head']['sha']

        log.msg('Processing GitHub PR #{}'.format(number),
                logLevel=logging.DEBUG)

        head_msg = yield self._get_commit_msg(repo_full_name, head_sha)
        if self._has_skip(head_msg):
            log.msg("GitHub PR #{}, Ignoring: "
                    "head commit message contains skip pattern".format(number))
            defer.returnValue(([], 'git'))

        action = payload.get('action')
        if action not in ('opened', 'reopened', 'synchronize'):
            log.msg("GitHub PR #{} {}, ignoring".format(number, action))
            defer.returnValue((pr_changes, 'git'))

        if not something_wants_pullrequests(payload):
            log.msg("GitHub PR#{}, Ignoring: no matching distro found".format(number))
            defer.returnValue(([], 'git'))

        properties = self.extractProperties(payload['pull_request'])
        properties.update({'event': event, 'prnumber': number})
        change = {
            'revision': payload['pull_request']['head']['sha'],
            'when_timestamp': dateparse(payload['pull_request']['created_at']),
            'branch': refname,
            'revlink': payload['pull_request']['_links']['html']['href'],
            'repository': payload['repository']['html_url'],
            'project': get_project_for_url(payload['pull_request']['base']['repo']['html_url'],
                                           payload['pull_request']['base']['ref']),
            'category': 'pull',
            # TODO: Get author name based on login id using txgithub module
            'author': payload['sender']['login'],
            'comments': u'GitHub Pull Request #{0} ({1} commit{2})\n{3}\n{4}'.format(
                number, commits, 's' if commits != 1 else '', title, comments),
            'properties': properties,
        }

        if callable(self._codebase):
            log.msg('_codebase is callable')
            change['codebase'] = self._codebase(payload)
        elif self._codebase is not None:
            change['codebase'] = self._codebase

        pr_changes.append(change)

        log.msg("Received {} changes from GitHub PR #{}".format(
            len(pr_changes), number))
        defer.returnValue((pr_changes, 'git'))


class AutobuilderForceScheduler(schedulers.ForceScheduler):
    # noinspection PyUnusedLocal,PyPep8Naming,PyPep8Naming
    @defer.inlineCallbacks
    def computeBuilderNames(self, builderNames=None, builderid=None):
        yield defer.returnValue(self.builderNames)


class AutobuilderConfig(object):
    def __init__(self, name, workers, repos, distros, layers):
        if name in settings.settings_dict():
            raise RuntimeError('Autobuilder config {} already exists'.format(name))
        self.name = name
        self.workers = []
        self.worker_cfgs = {}
        for w in workers:
            if isinstance(w, AutobuilderEC2Worker):
                self.workers.append(MyEC2LatentWorker(name=w.name,
                                                      password=w.password,
                                                      max_builds=w.max_builds,
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
                                                      block_device_map=w.ec2_dev_mapping,
                                                      spot_instance=w.ec2params.spot_instance,
                                                      build_wait_timeout=w.ec2params.build_wait_timeout,
                                                      max_spot_price=w.ec2params.max_spot_price,
                                                      price_multiplier=w.ec2params.price_multiplier,
                                                      instance_types=w.ec2params.instance_types))
            else:
                self.workers.append(worker.Worker(w.name, w.password, max_builds=w.max_builds))
            self.worker_cfgs[w.name] = w

        self.worker_names = [w.name for w in workers]

        self.repos = repos
        self.distros = distros or []
        self.layers = layers or []
        self.distrodict = {d.name: d for d in self.distros}
        for d in self.distros:
            if d.parallel_builders:
                d.builder_names = [d.name + '-' + imgset.name for imgset in d.targets]
            else:
                d.builder_names = [d.name]
        all_builder_names = [layer.name + '-checklayer' for layer in self.layers]
        for d in self.distros:
            all_builder_names += d.builder_names
        self.all_builder_names = sorted(all_builder_names)
        self.non_pr_scheduler_names = sorted([d.name for d in self.distros] +
                                             [d.name + '-force' for d in self.distros] +
                                             [layer.name + '-checklayer' for layer in self.layers] +
                                             [layer.name + '-checklayer-force' for layer in self.layers])
        self.pr_scheduler_names = sorted([d.name + '-pr' for d in self.distros if d.pullrequest_type] +
                                         [layer.name + '-checklayer-pr' for layer in self.layers])
        self.all_scheduler_names = sorted(self.non_pr_scheduler_names + self.pr_scheduler_names)
        self.codebasemap = {self.repos[r].uri: r for r in self.repos}
        settings.set_config_for_builder(name, self)

    def codebase_generator(self, change_dict):
        return self.codebasemap[change_dict['repository']]

    @property
    def change_sources(self):
        pollers = []
        for r in self.repos:
            if self.repos[r].pollinterval:
                branches = set()
                for d in self.distros:
                    if d.reponame == r and d.push_type:
                        branches.add(d.branch)
                for layer in self.layers:
                    if layer.reponame == r and layer.push_type:
                        branches.update(set(layer.branches))
                pollers.append(changes.GitPoller(self.repos[r].uri,
                                                 workdir='gitpoller-' + self.repos[r].name,
                                                 branches=sorted(branches),
                                                 category='push',
                                                 pollinterval=self.repos[r].pollinterval,
                                                 pollAtLaunch=True))
        return pollers

    @property
    def schedulers(self):
        s = []
        for layer in self.layers:
            if layer.pullrequests:
                s.append(schedulers.AnyBranchScheduler(name=layer.name + '-checklayer-pr',
                                                       change_filter=util.ChangeFilter(project=layer.name,
                                                                                       branch=layer.branches,
                                                                                       category=['pull']),
                                                       properties={'pullrequest': True},
                                                       treeStableTimer=layer.repotimer,
                                                       codebases=layer.codebases(self.repos),
                                                       builderNames=[layer.name + '-checklayer']))
            s.append(schedulers.AnyBranchScheduler(name=layer.name + '-checklayer',
                                                   change_filter=util.ChangeFilter(project=layer.name,
                                                                                   branch=layer.branches,
                                                                                   category=['push']),
                                                   treeStableTimer=layer.repotimer,
                                                   codebases=layer.codebases(self.repos),
                                                   builderNames=[layer.name + '-checklayer']))
            s.append(AutobuilderForceScheduler(name=layer.name + '-checklayer-force',
                                               codebases=layer.codebaseparamlist(self.repos),
                                               builderNames=[layer.name + '-checklayer']))
        for d in self.distros:
            if d.triggerable:
                s.append(schedulers.Triggerable(name=d.name + '-triggered',
                                                builderNames=d.builder_names))
            if d.push_type is not None:
                md_filter = util.ChangeFilter(project=d.name,
                                              branch=d.branch, codebase=d.reponame,
                                              category=['push'])
                props = {'buildtype': d.push_type}
                s.append(schedulers.SingleBranchScheduler(name=d.name,
                                                          change_filter=md_filter,
                                                          treeStableTimer=d.repotimer,
                                                          properties=props,
                                                          codebases=d.codebases(self.repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=d.builder_names))
            if d.pullrequest_type is not None:
                # No branch filter here - check is done in the event handler
                md_filter = util.ChangeFilter(project=d.name,
                                              codebase=d.reponame,
                                              category=['pull'])
                props = {
                    'buildtype': d.pullrequest_type,
                    'pullrequest': True
                }
                s.append(schedulers.SingleBranchScheduler(name=d.name + '-pr',
                                                          change_filter=md_filter,
                                                          properties=props,
                                                          codebases=d.codebases(self.repos),
                                                          createAbsoluteSourceStamps=True,
                                                          builderNames=d.builder_names))
            # noinspection PyTypeChecker
            forceprops = [util.ChoiceStringParameter(name='buildtype',
                                                     label='Build type',
                                                     choices=[bt.name for bt in d.buildtypes],
                                                     default=d.default_buildtype)]
            s.append(AutobuilderForceScheduler(name=d.name + '-force',
                                               codebases=d.codebaseparamlist(self.repos),
                                               properties=forceprops,
                                               builderNames=d.builder_names))
            if d.weekly_type is not None:
                slot = settings.get_weekly_slot()
                s.append(schedulers.Nightly(name=d.name + '-' + 'weekly',
                                            properties={'buildtype': d.weekly_type},
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
        for layer in self.layers:
            props = {'project': layer.name,
                     'repourl': self.repos[layer.reponame].uri,
                     'autobuilder': self.name,
                     'extraconf': layer.extra_config or []}
            repo = self.repos[layer.reponame]
            b.append(BuilderConfig(
                name=layer.name + '-checklayer',
                workernames=self.worker_names,
                nextWorker=nextEC2Worker,
                properties=props,
                factory=factory.CheckLayer(
                    repourl=repo.uri,
                    layerdir=layer.layerdir(repo.uri),
                    submodules=repo.submodules,
                    pokyurl=layer.pokyurl,
                    codebase=layer.reponame,
                    extra_env=layer.extra_env,
                    machines=layer.machines,
                    extra_options=layer.extra_options)
                ))
        for d in self.distros:
            props = {'artifacts_path': d.artifacts_path,
                     'project': d.name,
                     'repourl': self.repos[d.reponame].uri,
                     'branch': d.branch,
                     'setup_script': d.setup_script,
                     'artifacts': ','.join(d.artifacts),
                     'autobuilder': self.name,
                     'distro': d.name,
                     'extraconf': d.extra_config or []}
            repo = self.repos[d.reponame]
            if d.parallel_builders:
                b += [BuilderConfig(name=d.name + '-' + imgset.name,
                                    workernames=self.worker_names,
                                    nextWorker=nextEC2Worker,
                                    properties=props,
                                    factory=factory.DistroImage(repourl=repo.uri,
                                                                submodules=repo.submodules,
                                                                branch=d.branch,
                                                                codebase=d.reponame,
                                                                imagesets=[imgset],
                                                                triggers=d.triggers,
                                                                extra_env=d.extra_env))
                      for imgset in d.targets]
            else:
                b.append(BuilderConfig(
                    name=d.name,
                    workernames=self.worker_names,
                    nextWorker=nextEC2Worker,
                    properties=props,
                    factory=factory.DistroImage(repourl=repo.uri,
                                                submodules=repo.submodules,
                                                branch=d.branch,
                                                codebase=d.reponame,
                                                imagesets=d.targets,
                                                triggers=d.triggers,
                                                extra_env=d.extra_env)))
        return b


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
