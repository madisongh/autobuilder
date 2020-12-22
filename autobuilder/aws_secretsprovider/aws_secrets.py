import json
import boto3.session
from aws_secretsmanager_caching import SecretCache, SecretCacheConfig
from buildbot import config
from buildbot.secrets.providers.base import SecretProviderBase


class AWSSecretsManagerProvider(SecretProviderBase):
    name = "SecretInAWS"

    def checkConfig(self, region=None):
        if not isinstance(region, str):
            config.error("region parameter is {} instead of string".format(type(region)))

    def reconfigService(self, region=None):
        client = boto3.session.Session().client(service_name="secretsmanager", region_name=region)
        self.secrets = SecretCache(config=SecretCacheConfig(), client=client)

    def get(self, entry):
        name, key = entry.split('/', maxsplit=1)
        s_ent = self.secrets.get_secret_string(name)
        s_dict = json.loads(s_ent)
        try:
            return s_dict[key]
        except KeyError as e:
            raise KeyError("Could not find key {} for AWS Secrets Manager secret {}: {}".format(key, name, e)) from e
