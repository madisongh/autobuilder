import os
from buildbot.reporters.message import MessageFormatter, get_detected_status_text
from twisted.internet import defer
from twisted.python import log
import jinja2


@defer.inlineCallbacks
def getChangesForSourceStamps(master, sslist):
    log.msg("getChangesForSourceStamps: sslist=%s" % sslist)
    changelist = []
    for ss in sslist:
        changes = yield master.data.get(("sourcestamps", ss['ssid'], "changes"))
        changelist += changes
    defer.returnValue(changelist)


class AutobuilderMessageTemplate(object):
    def __init__(self, template_filename, template_dir=None):
        if template_dir is None:
            template_dir = os.path.join(os.path.dirname(__file__), "templates")
        with open(os.path.join(template_dir, template_filename), "r") as f:
            self.template = f.read()


# noinspection PyPep8Naming
class AutobuilderMessageFormatter(MessageFormatter):
    def __init__(self, template=None, summary=None, **kwargs):
        kwargs['template'] = template
        super().__init__(**kwargs)
        self.summary_template = summary
        self.want_properties = summary is not None

    @defer.inlineCallbacks
    def render_message_dict(self, master, context):
        yield self.buildAdditionalContext(master, context)
        context.update(self.context)
        body, subject, extra_info = yield defer.gatherResults(
            [
                defer.maybeDeferred(self.render_message_body, context),
                defer.maybeDeferred(self.render_message_subject, context),
                defer.maybeDeferred(self.render_message_extra_info, context),
            ],
            consumeErrors=True,
        )
        msgdict = {
            'body': body,
            'type': self.template_type,
            'subject': subject,
            'extra_info': extra_info
        }
        if 'changes' not in context:
            context['changes'] = []
        if self.summary_template is not None:
            summary = jinja2.Template(self.summary_template).render(context)
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
