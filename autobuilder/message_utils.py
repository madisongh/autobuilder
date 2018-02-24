from buildbot.reporters.message import MessageFormatter
from twisted.internet import defer


# noinspection PyPep8Naming
@defer.inlineCallbacks
def getChangesForBuild(master, buildid):
    dl = [master.data.get(("builds", buildid, "changes"))]
    changesd = yield defer.gatherResults(dl)
    changes = []
    for c in changesd:
        change = {'author': c['author'],
                  'comments': c['comments'],
                  'files': c['files'],
                  'revlink': c['revlink'],
                  'revision': c['revision']
                  }
        changes.append(change)
    defer.returnValue(changes)


class AutobuilderMessageFormatter(MessageFormatter):
    def buildAdditionalContext(self, master, ctx):
        ctx.update(self.ctx)
        ctx['changes'] = getChangesForBuild(master, ctx['build']['buildid'])
