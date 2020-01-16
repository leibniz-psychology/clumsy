"""
Regarding interactions with Kerberos: We could use python-kadmin here
(https://github.com/rjancewicz/python-kadmin), but it is not async-aware and
unmaintained since 2018.
Another option would be pexpect (https://github.com/pexpect/pexpect), which
has no 1st class support for asyncio though (i.e. spawning is not async).
Thus we roll out our own little version of expect to avoid specifying the
password on the commandline.
"""

import asyncio, subprocess

from sanic.log import logger

class KAdmException (Exception):
	pass

class KAdm:
	__slots__ = ('commonArgs', 'env')

	def __init__ (self, user, keytabFile, env=None):
		self.commonArgs = ['kadmin', '-k', '-t', keytabFile, '-p', user]
		# just for testing
		self.env = env

	async def addPrincipal (self, name, password):
		cmd = self.commonArgs + ['add_principal', '+requires_preauth', '-allow_svr', name]
		logger.debug (' '.join (cmd))
		proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)

		buf = await proc.stdout.read (512)
		assert buf.startswith (b'Enter password for principal '), buf
		proc.stdin.write (password.encode ('utf-8'))
		proc.stdin.write (b'\n')

		buf = await proc.stdout.read (512)
		assert buf.startswith (b'\nRe-enter password for principal '), buf
		proc.stdin.write (password.encode ('utf-8'))
		proc.stdin.write (b'\n')

		buf = await proc.stdout.read (512)
		assert buf == b'\n', buf

		proc.stdin.close ()

		ret = await proc.wait ()
		if ret != 0:
			raise KAdmException (buf)

	async def getPrincipal (self, name):
		cmd = self.commonArgs + ['get_principal', name]
		proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, env=self.env)
		buf = await proc.stdout.read ()
		buf = buf.decode ('utf-8')
		ret = await proc.wait ()
		if ret != 0:
			raise KeyError ('not found')
		princ = {}
		for l in buf.split ('\n'):
			try:
				k, v = l.split (': ', 1)
				princ[k] = v
			except ValueError:
				pass
		return princ

	async def deletePrincipal (self, name):
		cmd = self.commonArgs + ['delete_principal', '-force', name]
		proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL, env=self.env)
		ret = await proc.wait ()
		if ret != 0:
			raise KAdmException ()

