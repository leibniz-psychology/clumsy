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
Flush nscd’s caches

Must be run as root.
"""

import asyncio, subprocess

import structlog
from sanic import Blueprint, response

bp = Blueprint('nscdflushd')

@bp.route ('/account', methods=['DELETE'])
async def flushUserCache (request):
	"""
	Flush nscd’s user and group caches
	"""

	logger = structlog.get_logger ()

	# clear the last level cache (here, sssd) first
	cmd = ['sss_cache', '-U', '-G']
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	logger.info ('flush_sssd', command=cmd, ret=ret)
	if ret != 0:
		return response.json ({'status': 'sss_failed', 'code': ret}, status=500)

	# then the first level (here nscd)
	cmd = ['nscd', '-i', 'passwd', '-i', 'group']
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	logger.info ('flush_nscd', command=cmd, ret=ret)
	if ret != 0:
		return response.json ({'status': 'nscd_failed', 'code': ret}, status=500)

	return response.json ({'status': 'ok'}, status=200)

