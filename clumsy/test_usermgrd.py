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

from .usermgrd import withRollback

@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_rollback(success):
	# XXX: with python 3.8 we could use AsyncMock
	executed = []

	async def step (arg):
		executed.append (arg)

	def syncStep (arg):
		executed.append (arg)

	@withRollback
	async def dut (rollback):
		rollback.push_async_callback (step, 1)
		rollback.callback (syncStep, 2)
		rollback.push_async_callback (step, 3)
		if not success:
			raise Exception ()

	if success:
		await dut ()
	else:
		with pytest.raises (Exception):
			await dut ()
	expected = [] if success else [3, 2, 1]
	assert executed == expected

