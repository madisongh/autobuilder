import os
import buildbot.reporters.utils as utils
from buildbot.reporters.message import MessageFormatter
from buildbot.reporters.notifier import NotifierBase
from twisted.internet import defer
from twisted.python import log


@defer.inlineCallbacks
def getChangesForSourceStamps(master, sslist):
    log.msg("getChangesForSourceStamps: sslist=%s" % sslist)
    changelist = []
    for ss in sslist:
        changes = yield master.data.get(("sourcestamps", ss['ssid'], "changes"))
        changelist += changes
    defer.returnValue(changelist)


# noinspection PyPep8Naming
class AutobuilderMessageFormatter(MessageFormatter):
    def __init__(self, template_dir=None,
                 template_filename=None, template=None, template_name=None,
                 subject_filename=None, subject=None,
                 template_type=None, ctx=None,
                 wantProperties=True, wantSteps=False, wantLogs=False,
                 summary_filename=None, summary=None):
        if template_dir is None:
            template_dir = os.path.join(os.path.dirname(__file__), "templates")
        super(AutobuilderMessageFormatter, self).__init__(template_dir, template_filename,
                                                          template, template_name, subject_filename,
                                                          subject, template_type, ctx, wantProperties,
                                                          wantSteps, wantLogs)
        self.summary_template = None
        if summary_filename or summary:
            self.summary_template = self.getTemplate(summary_filename, template_dir, summary)
            self.wantProperties = True

    def renderMessage(self, ctx):
        msgdict = super(AutobuilderMessageFormatter, self).renderMessage(ctx)
        if self.summary_template is not None:
            msgdict['summary'] = self.summary_template.render(ctx)
        return msgdict

    @defer.inlineCallbacks
    def buildAdditionalContext(self, master, ctx):
        ctx.update(self.ctx)
        if ctx['sourcestamps']:
            ctx['changes'] = yield getChangesForSourceStamps(master, ctx['buildset']['sourcestamps'])
        else:
            ctx['changes'] = []
        ctx['buildset_status_detected'] = self.getDetectedStatus(ctx['mode'], ctx['buildset']['results'], None)


@defer.inlineCallbacks
def autoBuilderBuildMessage(self, name, builds, results):
    patches = []
    logs = []
    body = ""
    subject = None
    msgtype = None
    users = set()
    buildmsg = {}
    for build in builds:
        if self.addPatch:
            ss_list = build['buildset']['sourcestamps']

            for ss in ss_list:
                if 'patch' in ss and ss['patch'] is not None:
                    patches.append(ss['patch'])
        if self.addLogs:
            build_logs = yield self.getLogsForBuild(build)
            logs.extend(build_logs)

        if 'prev_build' in build and build['prev_build'] is not None:
            previous_results = build['prev_build']['results']
        else:
            previous_results = None
        blamelist = yield utils.getResponsibleUsersForBuild(self.master, build['buildid'])
        buildmsg = yield self.messageFormatter.formatMessageForBuildResults(
            self.mode, name, build['buildset'], build, self.master,
            previous_results, blamelist)
        users.update(set(blamelist))
        msgtype = buildmsg['type']
        body += buildmsg['body']
    if 'subject' in buildmsg:
        subject = buildmsg['subject']
    if 'summary' in buildmsg:
        body += buildmsg['summary']

    yield self.sendMessage(body, subject, msgtype, name, results, builds,
                           list(users), patches, logs)


NotifierBase.buildMessage = autoBuilderBuildMessage
