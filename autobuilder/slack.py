from buildbot.process.results import CANCELLED, EXCEPTION, FAILURE, SUCCESS, WARNINGS
from buildbot.reporters.notifier import NotifierBase
from buildbot.util import httpclientservice
from twisted.internet import defer
from twisted.python import log

COLORS = {
    CANCELLED: 'warning',
    EXCEPTION: 'warning',
    FAILURE: 'danger',
    SUCCESS: 'good',
    WARNINGS: 'warning'
}


class SlackNotifier(NotifierBase):

    # noinspection PyMethodOverriding
    def checkConfig(self, hook,
                    mode=("failing", "passing", "warnings"),
                    tags=None, builders=None,
                    buildSetSummary=False, messageFormatter=None,
                    subject="Buildbot %(result)s in %(title)s on %(builder)s",
                    schedulers=None, branches=None,
                    colors=None, base_url='https://hooks.slack.com/services',
                    watchedWorkers=None, messageFormatterMissingWorker=None):
        super(SlackNotifier, self).checkConfig(mode, tags, builders,
                                               buildSetSummary, messageFormatter,
                                               subject, False, False,
                                               schedulers,
                                               branches, watchedWorkers)

        httpclientservice.HTTPClientService.checkAvailable(self.__class__.__name__)

    # noinspection PyAttributeOutsideInit,PyAttributeOutsideInit,PyAttributeOutsideInit,PyMethodOverriding
    @defer.inlineCallbacks
    def reconfigService(self, hook,
                        mode=("failing", "passing", "warnings"),
                        tags=None, builders=None,
                        buildSetSummary=False, messageFormatter=None,
                        subject="Buildbot %(result)s in %(title)s on %(builder)s",
                        schedulers=None, branches=None,
                        colors=None, base_url='https://hooks.slack.com/services',
                        watchedWorkers=None, messageFormatterMissingWorker=None):
        super(SlackNotifier, self).reconfigService(mode, tags, builders,
                                                   buildSetSummary, messageFormatter,
                                                   subject, False, False,
                                                   schedulers, branches,
                                                   watchedWorkers, messageFormatterMissingWorker)
        self.hook = hook
        self.colors = colors if colors is not None else COLORS
        self._http = yield httpclientservice.HTTPClientService.getService(
            self.master, base_url)

    # noinspection PyShadowingBuiltins
    @defer.inlineCallbacks
    def sendMessage(self, body, subject=None, type='plain', builderName=None,
                    results=None, builds=None, users=None, patches=None,
                    logs=None, worker=None):
        msgtext = "%s\n%s" % (subject, body)
        msg = {'attachments': [{'color': self.colors.get(results, 'warning'), 'text': msgtext}]}
        response = yield self._http.post(self.hook, json=msg)
        if response.code != 200:
            log.msg("POST response code %s: %s" % (response.code, response.content))
