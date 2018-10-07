from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='1.0.3',
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
    install_requires=['buildbot[tls]>=1.4.0',
                      'buildbot-worker>=1.4.0',
                      'buildbot-www>=1.4.0',
                      'buildbot-console-view>=1.4.0',
                      'buildbot-grid-view>=1.4.0',
                      'buildbot-waterfall-view>=1.4.0'
                      'buildbot-badges>=1.4.0',
                      'boto3', 'botocore',
                      'treq', 'twisted',
                      'python-dateutil']
)
