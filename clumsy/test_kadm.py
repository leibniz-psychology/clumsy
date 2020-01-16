import asyncio
from tempfile import NamedTemporaryFile

import pytest
from k5test import K5Realm

from .kadm import KAdm

@pytest.fixture
def realm ():
	realm = K5Realm(start_kadmind=True)
	yield realm
	realm.stop()
	del realm

@pytest.fixture
def kadm (realm):
	realm.extract_keytab (realm.admin_princ, realm.keytab)
	kadm = KAdm (realm.admin_princ, realm.keytab, env=realm.env)
	yield kadm

@pytest.mark.asyncio
async def test_add_delete (kadm, realm):
	u = 'anotheruser'

	with pytest.raises (KeyError):
		obj = await kadm.getPrincipal (u)

	await kadm.addPrincipal (u, 'password')
	obj = await kadm.getPrincipal (u)
	assert obj['Principal'] == f'{u}@{realm.realm}'

	await kadm.deletePrincipal (u)

	with pytest.raises (KeyError):
		obj = await kadm.getPrincipal (u)

@pytest.mark.asyncio
async def test_get (kadm, realm):
	# try existing user
	for u in (realm.user_princ, realm.admin_princ, realm.host_princ):
		obj = await kadm.getPrincipal (u)
		assert obj['Principal'] == u

	# nonexisting
	for u in ('nonexisting', f'nonexisting@{realm.realm}'):
		with pytest.raises (KeyError):
			obj = await kadm.getPrincipal (u)

