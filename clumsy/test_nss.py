import pytest

from .nss import getUser

def test_getUser_name ():
	""" Test by name """
	# assuming root exists on any system
	u = 'root'
	o = getUser (u)
	assert o['name'] == u
	assert o['uid'] == 0
	assert o['gid'] == 0
	assert o['homedir'] == '/root'

def test_getUser_uid ():
	""" Test by uid """
	u = 0
	o = getUser (u)
	assert o['name'] == 'root'
	assert o['uid'] == 0
	assert o['gid'] == 0
	assert o['homedir'] == '/root'

def test_getUser_invalid ():
	with pytest.raises (ValueError):
		getUser (dict ())

def test_getUser_nonexistent ():
	with pytest.raises (KeyError):
		getUser ('nonexistent-user')

	with pytest.raises (KeyError):
		getUser (-1)

