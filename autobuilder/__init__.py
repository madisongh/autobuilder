from .hackery import myESMTPSender
from .abconfig import AutobuilderConfig, Repo
from .workers.config import EC2Params, AutobuilderWorker, AutobuilderEC2Worker
from .distros.config import Distro, TargetImageSet, TargetImage, SdkImage, Buildtype
from .layers.config import Layer
from .factory.distro import DistroImage
from .github.handler import AutobuilderGithubEventHandler
from .aws_secretsprovider.aws_secrets import AWSSecretsManagerProvider
from .message_utils import AutobuilderMessageFormatter, AutobuilderMessageTemplate

import twisted
from twisted.mail.smtp import ESMTPSenderFactory

if twisted.version.major == 20 and twisted.version.minor <= 3:
    ESMTPSenderFactory.protocol = myESMTPSender
