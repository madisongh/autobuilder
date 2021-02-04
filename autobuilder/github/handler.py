import logging

from buildbot.changes.changes import Change
from buildbot.www.hooks.github import GitHubEventHandler
from dateutil.parser import parse as dateparse
from twisted.internet import defer
from twisted.python import log

from autobuilder.abconfig import ABCFG_DICT


def get_project_for_url(repo_urls, branch):
    for abcfg, cfg in ABCFG_DICT.items():
        for repo_url in repo_urls:
            if repo_url in cfg.codebasemap:
                reponame = cfg.codebasemap[repo_url]
                for layer in cfg.layers:
                    if layer.reponame == reponame and branch in layer.branches:
                        log.msg('Found layer {} for repo {} and branch {}'.format(layer.name, reponame, branch))
                        return repo_url, layer.name
                log.msg("get_project_for_url: {}/{} not found in layers".format(reponame, branch))
                for distro in cfg.distros:
                    if distro.reponame == reponame and distro.branch == branch:
                        log.msg('Found distro {} for repo {} and branch {}'.format(distro.name, reponame, branch))
                        if distro.push_type:
                            log.msg('Distro {} wants pushes'.format(distro.name))
                            return repo_url, distro.name
                log.msg("get_project_for_url: {}/{} not found in distros".format(reponame, branch))
            else:
                log.msg("get_project_for_url: url {} not found".format(repo_url))
    return None, None


def layer_pr_filter(change: Change) -> bool:
    target_branch = change.properties.getProperty('basename')
    if target_branch is None:
        return False
    for abcfg, cfg in ABCFG_DICT.items():
        try:
            layer = cfg.layerdict[change.project]
        except KeyError:
            log.msg("layer_pr_filter: no layer for {}".format(change.project))
            layer = None
        if layer is not None:
            if target_branch in layer.branches:
                log.msg("layer_pr_filter: match for layer {} (branch {})".format(layer.name, target_branch))
                return True
    log.msg("layer_pr_filter: no match for project {} branch {}".format(change.project, target_branch))
    return False


def codebasemap_from_github_payload(payload):
    if 'pull_request' in payload:
        base = payload['pull_request']['base']
        urls = [base['repo']['html_url'],
                base['repo']['clone_url'],
                base['repo']['git_url'],
                base['repo']['ssh_url']]
    else:
        urls = [payload['repository']['html_url'],
                payload['repository']['clone_url'],
                payload['repository']['ssh_url'],
                payload['repository']['git_url']]
    reponame = ''
    for abcfg, cfg in ABCFG_DICT.items():
        for url in urls:
            try:
                reponame = cfg.codebasemap[url]
                break
            except KeyError:
                pass
    return reponame


def something_wants_pullrequests(payload):
    if 'pull_request' not in payload:
        log.msg('something_wants_pullrequests called for a non-PR?')
        return False
    base = payload['pull_request']['base']
    urls = [base['repo']['html_url'],
            base['repo']['clone_url'],
            base['repo']['git_url'],
            base['repo']['ssh_url']]
    basebranch = base['ref']
    for abcfg, cfg in ABCFG_DICT.items():
        for url in urls:
            if url in cfg.codebasemap:
                reponame = cfg.codebasemap[url]
                for layer in cfg.layers:
                    if layer.reponame == reponame and basebranch in layer.branches:
                        log.msg('Found layer {} for repo {} and branch {}, returning {}'.format(layer.name,
                                                                                                reponame,
                                                                                                basebranch,
                                                                                                layer.pullrequests))
                        return layer.pullrequests
                for distro in cfg.distros:
                    if distro.reponame == reponame and distro.branch == basebranch:
                        log.msg('Found distro {} for repo {} and branch {}'.format(distro.name, reponame, basebranch))
                        if distro.pullrequest_type:
                            log.msg('Distro {} wants pull requests'.format(distro.name))
                            return True
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
        repo = payload['repository']
        repo_urls = [repo[u] for u in ['html_url', 'clone_url', 'git_url', 'ssh_url']]
        ref = payload['ref']
        if not ref.startswith('refs/heads/'):
            log.msg('Ignoring non-branch push (ref: {})'.format(ref))
            return [], 'git'
        branch = ref.split('/')[-1]
        repo_url, project = get_project_for_url(repo_urls, branch)
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
        base = payload['pull_request']['base']
        basename = base['ref']
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
            return [], 'git'

        action = payload.get('action')
        if action not in ('opened', 'reopened', 'synchronize'):
            log.msg("GitHub PR #{} {}, ignoring".format(number, action))
            return pr_changes, 'git'

        if not something_wants_pullrequests(payload):
            log.msg("GitHub PR #{}, Ignoring: no matching distro found".format(number))
            return [], 'git'

        files = yield self._get_pr_files(repo_full_name, number)

        properties = self.extractProperties(payload['pull_request'])
        properties.update({'event': event, 'prnumber': number})
        properties.update({'basename': basename})
        urls = [base['repo']['html_url'],
                base['repo']['clone_url'],
                base['repo']['git_url'],
                base['repo']['ssh_url']]
        repository, project = get_project_for_url(urls, basename)
        change = {
            'revision': payload['pull_request']['head']['sha'],
            'when_timestamp': dateparse(payload['pull_request']['created_at']),
            'branch': refname,
            'files': files,
            'revlink': payload['pull_request']['_links']['html']['href'],
            'repository': repository,
            'project': project,
            'category': 'pull',
            # TODO: Get author name based on login id using txgithub module
            'author': payload['sender']['login'],
            'comments': u'GitHub Pull Request #{0} ({1} commit{2})\n{3}\n{4}'.format(
                number, commits, 's' if commits != 1 else '', title, comments),
            'properties': properties,
        }

        if callable(self._codebase):
            change['codebase'] = self._codebase(payload)
        elif self._codebase is not None:
            change['codebase'] = self._codebase

        pr_changes.append(change)

        log.msg("Received {} changes from GitHub PR #{}".format(
            len(pr_changes), number))
        return pr_changes, 'git'
