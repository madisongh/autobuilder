from setuptools import setup, find_packages

BUILDBOTVERSION = '2.8.4'

setup(
    name='autobuilder',
    version='2.7.3',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    include_package_data=True,
    package_data={
        'autobuilder': ['templates/*.txt']
    },
    install_requires=['aws-secretsmanager-caching',
                      'buildbot[tls]>=' + BUILDBOTVERSION,
                      'buildbot-www>=' + BUILDBOTVERSION,
                      'buildbot-console-view>=' + BUILDBOTVERSION,
                      'buildbot-grid-view>=' + BUILDBOTVERSION,
                      'buildbot-waterfall-view>=' + BUILDBOTVERSION,
                      'buildbot-badges>=' + BUILDBOTVERSION,
                      'boto3', 'botocore',
                      'treq', 'twisted',
                      'python-dateutil',
                      'jinja2']
)
