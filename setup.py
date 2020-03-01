from setuptools import setup, find_packages

BUILDBOTVERSION = '2.7.0'

setup(
    name='autobuilder',
    version='2.4.1',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    entry_points={
        'console_scripts': [
            'store-artifacts = autobuilder.scripts.store_artifacts:main',
        ]
    },
    include_package_data=True,
    package_data={
        'autobuilder': ['templates/*.txt']
    },
    install_requires=['aws-secretsmanager-caching',
                      'buildbot[tls]>=' + BUILDBOTVERSION,
                      'buildbot-worker>=' + BUILDBOTVERSION,
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
