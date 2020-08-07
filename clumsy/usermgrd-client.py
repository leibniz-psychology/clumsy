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

