from buildbot.status.web.hooks.github import GitHubEventHandler
from twisted.python import log
import abconfig


def codebasemap(payload):
    return abconfig.get_project_for_url(payload['repository']['url'])


class AutobuilderGithubEventHandler(GitHubEventHandler):

    # noinspection PyMissingConstructor
    def __init__(self, secret, strict, codebase=None):
        if codebase is None:
            codebase = codebasemap
        GitHubEventHandler.__init__(self, secret, strict, codebase)

    def handle_push(self, payload):
        # This field is unused:
        user = None
        # user = payload['pusher']['name']
        repo = payload['repository']['name']
        repo_url = payload['repository']['url']
        # NOTE: what would be a reasonable value for project?
        # project = request.args.get('project', [''])[0]
        project = abconfig.get_project_for_url(repo_url,
                                               default_if_not_found=payload['repository']['full_name'])

        changes = self._process_change(payload, user, repo, repo_url, project)

        log.msg("Received %d changes from github" % len(changes))

        return changes, 'git'
