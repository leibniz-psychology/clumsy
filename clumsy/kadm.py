# Copyright 2019–2020 Leibniz Institute for Psychology
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

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

	async def addPrincipal (self, name, password, expire='never'):
		cmd = self.commonArgs + [
				'add_principal',
				'+requires_preauth',
				'-allow_svr',
				'-expire', expire,
				name,
				]
		logger.debug (' '.join (cmd))
		proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env)

		buf = await proc.stdout.read (512)
		assert buf.startswith (b'Enter password for principal '), buf
		proc.stdin.write (password.encode ('utf-8') + b'\n')

		buf = await proc.stdout.read (512)
		assert buf.startswith (b'\nRe-enter password for principal '), buf
		proc.stdin.write (password.encode ('utf-8') + b'\n')

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

