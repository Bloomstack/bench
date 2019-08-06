class InvalidBranchException(Exception):
	pass


class InvalidRemoteException(Exception):
	pass


class PatchError(Exception):
	pass


class CommandFailedError(Exception):
	pass


class MajorVersionUpgradeException(Exception):
	def __init__(self, message, upstream_version, local_version):
		super(MajorVersionUpgradeException, self).__init__(message)
		self.upstream_version = upstream_version
		self.local_version = local_version
