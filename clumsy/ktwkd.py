"""
Kill Them With Kindness daemon

Sends orphaned processes (invoking user does not exist any more) their last
meal.

Use::

	setpriv --reuid=500000 --inh-caps=-all -- sleep 1h

as root to test this program works.
"""

import asyncio, os, re, signal

from .nss import getUser

spaces = re.compile ('\s+')

def readStatus (pid):
	""" Read process status and yield items as key, value pairs """
	with open (f'/proc/{pid}/status') as fd:
		for l in fd:
			k, v = l.split (':', 1)
			k = k.strip ()
			v = v.strip ()
			yield k, v

async def ktwkd ():
	minuid = 1000

	while True:
		print ('searching for orphaned procs')
		# Yes, psutil exists. No, I’m not using it.
		for x in os.listdir('/proc'):
			if x.isdigit():
				pid = int (x)
				try:
					for k, v in readStatus (pid):
						if k == 'Uid':
							real, effective, saved, filesystem = map (int, spaces.split (v))
							if real >= minuid:
								try:
									user = getUser (real)
								except KeyError:
									print (f'killing pid {pid} user {real}')
									try:
										os.kill (pid, signal.SIGKILL)
									except PermissionError:
										print (f'cannot kill {pid}, are you root?')
									except ProcessLookupError:
										# already gone
										pass
							break
				except FileNotFoundError:
					# probably a race-condition (i.e. process died)
					pass
		await asyncio.sleep (60)

__all__ = ['ktwkd']

