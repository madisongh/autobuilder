import os
from buildbot.reporters.message import MessageFormatter, get_detected_status_text
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
    def __init__(self, template_dir=None, template_name=None,
                 summary_filename=None, summary=None, **kwargs):
        if template_dir is None:
            template_dir = os.path.join(os.path.dirname(__file__), "templates")
        kwargs['template_dir'] = template_dir
        super().__init__(template_name, **kwargs)
        self.summary_template = None
        if summary_filename or summary:
            self.summary_template = self.getTemplate(summary_filename, template_dir, summary)
            self.wantProperties = True

    @defer.inlineCallbacks
    def render_message_dict(self, master, context):
        yield self.buildAdditionalContext(master, context)
        context.update(self.context)
        msgdict = {
            'body': self.render_message_body(context),
            'type': self.template_type,
            'subject': self.render_message_subject(context)
        }
        if 'changes' not in context:
            context['changes'] = []
        if self.summary_template is not None:
            summary = self.summary_template.render(context)
            if msgdict['body'] is None:
                msgdict['body'] = summary
            else:
                msgdict['body'] += summary
        return msgdict

    @defer.inlineCallbacks
    def buildAdditionalContext(self, master, context):
        context.update(self.context)
        if context['sourcestamps']:
            context['changes'] = yield getChangesForSourceStamps(master, context['buildset']['sourcestamps'])
        else:
            context['changes'] = []
        context['buildset_status_detected'] = get_detected_status_text(context['mode'],
                                                                       context['buildset']['results'], None)
