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
Kill Them With Kindness daemon

Sends orphaned processes (invoking user does not exist any more) their last
meal.

Use::

	setpriv --reuid=500000 --inh-caps=-all -- sleep 1h

as root to test this program works.
"""

import asyncio, os, re, signal, logging
from collections import namedtuple

from .nss import getUser

spaces = re.compile ('\s+')

UidSet = namedtuple ('UidSet', ['real', 'effective', 'saved', 'filesystem'])

class Process:
	__slots__ = ('pid', 'uid')

	def __init__ (self, pid):
		self.pid = pid
		try:
			for k, v in self._readStatus ():
				if k == 'Uid':
					self.uid = UidSet (*map (int, spaces.split (v)))
		except FileNotFoundError:
			raise ProcessLookupError ()

	def _readStatus (self):
		""" Read process status and yield items as key, value pairs """
		with open (f'/proc/{self.pid}/status') as fd:
			for l in fd:
				k, v = l.split (':', 1)
				k = k.strip ()
				v = v.strip ()
				yield k, v

	def kill (self, signal):
		return os.kill (self.pid, signal)

	@classmethod
	def all (cls):
		for x in os.listdir('/proc'):
			if x.isdigit():
				try:
					yield cls (int (x))
				except ProcessLookupError:
					# already gone
					pass

async def ktwkd ():
	minuid = 1000

	while True:
		logging.debug ('searching for orphaned procs')
		# Yes, psutil exists. No, I’m not using it.
		for p in Process.all ():
			if p.uid.real >= minuid:
				try:
					user = getUser (p.uid.real)
				except KeyError:
					logging.info (f'killing pid {p.pid} user {p.uid.real}')
					try:
						p.kill (signal.SIGKILL)
					except PermissionError:
						logging.error (f'cannot kill {p.pid}, are you root?')
					except ProcessLookupError:
						# already gone
						pass
		await asyncio.sleep (60)

__all__ = ['ktwkd']

