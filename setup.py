from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='2.2.0',
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
    install_requires=['buildbot[tls]>=2.4.1',
                      'buildbot-worker>=2.4.1',
                      'buildbot-www>=2.4.1',
                      'buildbot-console-view>=2.4.1',
                      'buildbot-grid-view>=2.4.1',
                      'buildbot-waterfall-view>=2.4.1'
                      'buildbot-badges>=2.4.1',
                      'boto3', 'botocore',
                      'treq', 'twisted',
                      'python-dateutil',
                      'jinja2']
)
