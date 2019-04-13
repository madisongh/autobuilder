from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='1.1.1',
    packages=find_packages(),
    license='MIT',
    author='Matt Madison',
    author_email='matt@madison.systems',
    entry_points={
        'console_scripts': [
            'update-sstate-mirror = autobuilder.scripts.update_sstate_mirror:main',
            'update-downloads = autobuilder.scripts.update_downloads:main',
            'install-sdk = autobuilder.scripts.install_sdk:main',
            'autorev-report = autobuilder.scripts.autorev_report:main'
        ]
    },
    include_package_data=True,
    package_data={
        'autobuilder': ['templates/*.txt']
    },
    install_requires=['buildbot[tls]>=2.2.0',
                      'buildbot-worker>=2.2.0',
                      'buildbot-www>=2.2.0',
                      'buildbot-console-view>=2.2.0',
                      'buildbot-grid-view>=2.2.0',
                      'buildbot-waterfall-view>=2.2.0'
                      'buildbot-badges>=2.2.0',
                      'boto3', 'botocore',
                      'treq', 'twisted',
                      'python-dateutil']
)
