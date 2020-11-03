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

import asyncio, sys

import aiohttp

def socketSession (path):
	conn = aiohttp.UnixConnector (path=path)
	return aiohttp.ClientSession(connector=conn)

async def create (path, user):
	async with socketSession (path) as usermgrd:
		form = {'firstName': 'abc', 'lastName': 'abc', 'username': user,
				'orcid': 'abc', 'authorization': 'abc', 'email': 'abc'}
		async with usermgrd.post ('http://localhost/', json=form) as resp:
			data = await resp.json ()
			print (data)

async def delete (path, user):
	async with socketSession (path) as usermgrd:
		async with usermgrd.delete (f'http://localhost/{user}') as resp:
			data = await resp.json ()
			print (data)

if __name__ == '__main__':
	action, path, user = sys.argv[1:4]

	if action == 'create':
		asyncio.run (create (path, user))
	elif action == 'delete':
		asyncio.run (delete (path, user))
	else:
		assert False

