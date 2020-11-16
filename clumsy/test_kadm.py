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

