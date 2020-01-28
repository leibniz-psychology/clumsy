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

