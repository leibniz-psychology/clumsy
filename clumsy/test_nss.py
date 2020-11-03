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

