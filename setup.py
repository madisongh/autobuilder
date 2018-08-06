from setuptools import setup, find_packages

setup(
    name='autobuilder',
    version='1.0.0',
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
    install_requires=['buildbot>=1.1.0',
                      'boto3', 'botocore',
                      'twisted']
)
