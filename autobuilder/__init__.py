from .hackery import myESMTPSender
from .abconfig import AutobuilderConfig, Buildtype, Distro, Repo, TargetImageSet
from .abconfig import TargetImage, SdkImage, Layer
from .abconfig import AutobuilderWorker, EC2Params, AutobuilderEC2Worker
from .abconfig import AutobuilderGithubEventHandler
from .ec2 import MyEC2LatentWorker
from .aws_secretsprovider.aws_secrets import AWSSecretsManagerProvider
from .message_utils import AutobuilderMessageTemplate, AutobuilderMessageFormatter
import twisted
from twisted.mail.smtp import ESMTPSenderFactory

if twisted.version.major == 20 and twisted.version.minor <= 3:
    ESMTPSenderFactory.protocol = myESMTPSender
