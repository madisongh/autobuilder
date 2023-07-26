from abc import ABCMeta
from twisted.mail.smtp import ESMTPSender
from buildbot.steps.source.git import Git


class myESMTPSender(ESMTPSender, metaclass=ABCMeta):
    def _getContextFactory(self):
        if self.context is not None:
            return self.context
        try:
            from twisted.internet import ssl
        except ImportError:
            return None
        else:
            try:
                context = ssl.ClientContextFactory()
                return context
            except AttributeError:
                return None


class myGit(Git):
    renderables = ["repourl", "reference", "branch",
                   "codebase", "mode", "method", "origin", "config"]
