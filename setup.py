from setuptools import setup

setup(
    name='clumsy',
    version='0.1',
    author='Lars-Dominik Braun',
    author_email='ldb@leibniz-psychology.org',
    #url='https://',
    packages=['clumsy'],
    #license='LICENSE.txt',
    description='Cluster Management Services Y',
    #long_description=open('README.rst').read(),
    long_description_content_type='text/x-rst',
    install_requires=[
        'sanic',
		# XXX: version 4 is currently broken, blacklist it
        'aiohttp<4',
        'bonsai',
    ],
    python_requires='>=3.7',
    entry_points={
    'console_scripts': [
            'clumsy = clumsy:main',
            ],
    },
)
