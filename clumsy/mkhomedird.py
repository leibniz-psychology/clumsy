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
Handle homedir creation/deleteion

Needs permission to create new directory in home basedir and to chown them to
the proper user (CAP_CHOWN). Users must be present in local user database (via
nss).
"""

import os, shutil, asyncio, subprocess, secrets, stat
from datetime import datetime, timedelta

from sanic import Blueprint, response
from sanic.log import logger

from .nss import getUser

def randomSecret (n=32):
	alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
	return ''.join (secrets.choice (alphabet) for i in range (n))

running = set ()
deleteToken = dict ()

bp = Blueprint('mkhomedird')

async def copyDir (a, b, uid, gid):
	"""
	Copy directory a to b using rsync
	"""
	def addSlash (x):
		return x if x.endswith ('/') else x+'/'

	# make sure dirs end with / for rsync
	a = addSlash (a)
	b = addSlash (b)
	cmd = ['rsync', '-av', f'--chown={uid}:{gid}', a, b]
	logger.debug (' '.join (cmd))
	proc = await asyncio.create_subprocess_exec (*cmd, stdin=subprocess.DEVNULL)
	ret = await proc.wait ()
	return ret == 0

@bp.route ('/<user>', methods=['POST'])
async def touchHome (request, user):
	"""
	Create a user’s home

	User must exist and have a valid, but nonexistent homedir set
	"""

	config = request.app.config

	if user in running:
		# XXX: wait for response and return it
		return response.json ({'status': 'in_progress'}, status=202)
	running.add (user)

	try:
		try:
			userdata = getUser (user)
		except KeyError:
			return response.json ({'status': 'user_not_found'}, status=404)

		mode = 0o750
		for d, settings in config.DIRECTORIES.items ():
			d = d.format (**userdata)
			create = settings.get ('create', False)
			if not create:
				continue

			logger.debug (f'creating directory {d} with {settings}')
			try:
				os.mkdir (d, mode=mode)
				os.chown (d, userdata["uid"], userdata["gid"])
			except FileExistsError:
				return response.json ({'status': 'homedir_exists'}, status=409)

			if isinstance (create, str):
				if not await copyDir (create, d, userdata['uid'], userdata['gid']):
					return response.json ({'status': 'copy_skeleton_failed'}, status=500)
				# make sure the directory has proper permissions after rsync messes them up
				os.chmod (d, mode)

	finally:
		running.remove (user)

	return response.json ({'status': 'ok'}, status=201)

def remove_readonly(func, path, _):
	"Clear the readonly bit and reattempt the removal"
	os.chmod(path, stat.S_IWRITE)
	func(path)

async def revokeAcl (uid, gid, dirs):
	args = ['setfacl',
			'-R',
			'-x', f'u:{uid}',
			'-x', f'd:u:{uid}',
			'-x', f'g:{gid}',
			'-x', f'd:g:{gid}',
			'--',
			] + dirs
	logger.debug (f'Removing ACL for {uid}/{gid} in {dirs}')
	proc = await asyncio.create_subprocess_exec (*args,
			stdout=asyncio.subprocess.PIPE,
			stderr=asyncio.subprocess.PIPE)
	stdout, stderr = await proc.communicate()
	logger.debug (f'setfacl reported {stdout} {stderr}')

@bp.route ('/<user>', methods=['DELETE'])
async def deleteHome (request, user):
	"""
	Delete a user’s homedir

	Two-step verification needed: First, call without a token query string to
	obtain a token, then delete the user and call again with token.

	XXX: make sure homedir fits a certain pattern (to avoid arbitrary dir deletion)
	"""

	config = request.app.config
	token = request.args.get ('token')

	if not token:
		# get a new token
		try:
			userdata = getUser (user)
		except KeyError:
			return response.json ({'status': 'user_not_found'}, status=404)

		while True:
			newToken = randomSecret ()
			if newToken in deleteToken:
				continue
			deleteToken[newToken] = (datetime.utcnow (), userdata)
			return response.json ({'status': 'again', 'token': newToken})
	else:
		try:
			date, userdata = deleteToken[token]
			if user != userdata['name']:
				raise KeyError ('wrong user')
		except KeyError:
			return response.json ({'status': 'token_invalid'}, status=403)

		# token is not expired
		if datetime.utcnow () - date > timedelta (seconds=60):
			return response.json ({'status': 'token_expired'}, status=403)

		# make sure the user is actually gone
		try:
			currentUserdata = getUser (userdata['name'])
			return response.json ({'status': 'user_exists'}, status=403)
		except KeyError:
			pass

		dirs = list (map (lambda x: x.format (**userdata), config.DIRECTORIES.keys ()))
		for d in dirs:
			if os.path.exists (d):
				logger.debug (f'deleting directory {d}')
				shutil.rmtree (d, onerror=remove_readonly)
		# The actual directory will be gone, but we can revoke
		# one level up.
		await revokeAcl (userdata['uid'], userdata['gid'],
				[os.path.dirname (x) for x in dirs])

		return response.json ({'status': 'ok'})

