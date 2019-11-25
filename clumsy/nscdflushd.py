"""
Flush nscd’s caches
"""

import asyncio, subprocess

from sanic import Sanic, Blueprint, response
from sanic.log import logger

bp = Blueprint('nscdflushd')

@bp.route ('/account', methods=['DELETE'])
async def flushUserCache (request):
	"""
	Flush nscd’s user and group caches
	"""

	# clear the last level cache (here, sssd) first
	cmd = ['sss_cache', '-U', '-G']
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	if ret != 0:
		return response.json ({'status': 'sss_failed', 'code': ret}, status=500)

	# then the first level (here nscd)
	cmd = ['nscd', '-i', 'passwd', '-i', 'group']
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	if ret != 0:
		return response.json ({'status': 'nscd_failed', 'code': ret}, status=500)

	return response.json ({'status': 'ok'}, status=200)

