"""
Autobuilder configuration class.
"""
from twisted.internet import defer
from buildbot.plugins import changes, schedulers, worker
from autobuilder import settings
from workers.config import AutobuilderEC2Worker
from workers.ec2 import MyEC2LatentWorker

ABCFG_DICT = {}


def get_config_for_builder(name):
    return ABCFG_DICT[name]


def set_config_for_builder(name, val):
    ABCFG_DICT[name] = val


def settings_dict():
    return ABCFG_DICT


class Repo(object):
    def __init__(self, name, uri, pollinterval=None,
                 submodules=False):
        self.name = name
        self.uri = uri
        self.pollinterval = pollinterval
        self.submodules = submodules


class AutobuilderForceScheduler(schedulers.ForceScheduler):
    # noinspection PyUnusedLocal,PyPep8Naming,PyPep8Naming
    @defer.inlineCallbacks
    def computeBuilderNames(self, builderNames=None, builderid=None):
        yield defer.returnValue(self.builderNames)


class AutobuilderConfig(object):
    def __init__(self, name, workers, repos, distros, layers):
        if name in settings_dict():
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
        for layer in self.layers:
            layer.abconfig = self.name
        self.distrodict = {d.name: d for d in self.distros}
        self.layerdict = {layer.name: layer for layer in self.layers}
        for d in self.distros:
            d.abconfig = self.name
        self.codebasemap = {self.repos[r].uri: r for r in self.repos}
        set_config_for_builder(name, self)
        self._builders = None
        self._schedulers = None

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
        if self._schedulers is None:
            self._schedulers = []
            for layer in self.layers:
                self._schedulers += layer.schedulers
            for d in self.distros:
                self._schedulers += d.schedulers
        return self._schedulers

    @property
    def builders(self):
        if self._builders is None:
            self._builders = []
            for layer in self.layers:
                self._builders += layer.builders

            for d in self.distros:
                self._builders += d.builders
        return self._builders

    @property
    def all_builder_names(self):
        return sorted([b.name for b in self.builders])

    @property
    def non_pr_scheduler_names(self):
        return sorted([s.name for s in self.schedulers if not s.name.endswith('-pr')])
