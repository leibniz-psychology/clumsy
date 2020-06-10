import pytest

from .usermgrd import withRollback, possibleUsernames, UserInfo

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

def test_possibleUsernames ():
	# basic
	assert list (possibleUsernames (UserInfo (
			username='foobar',
			firstName='foo',
			lastName='bar')))[0:4] == ['foobar', 'fbar', 'foobar1', 'fbar1']

	# foreign names
	assert list (possibleUsernames (UserInfo (
			firstName='هنا',
			lastName='لطيف')))[0:2] == ['hltyf', 'hltyf1']

	# long names
	assert list (possibleUsernames (UserInfo (
			username='veryverylongusernamerequested',
			firstName='MyLongFirstName',
			lastName='MyLongLastName'), maxlen=10))[0:4] == ['veryverylo', 'mmylonglas', 'veryveryl1', 'mmylongla1']

