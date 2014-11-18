"""
Repository class.
"""


class Repo(object):
    def __init__(self, name, uri, poll=True, pollinterval=300, project=None,
                 submodules=False):
        self.name = name
        self.uri = uri
        self.poll = poll
        self.pollinterval = pollinterval
        self.project = project or name
        self.submodules = submodules
